# -*- coding: utf-8 -*-
"""
scan7_hw.py  —  7핀 스캔셀 보드 하드웨어 제어 계층 (Jetson Nano, Python 3.6 이상)

이 파일은 "보드를 어떻게 움직이는가" 만 담당합니다.
테스트 벡터를 읽고 판정하는 일은 scan7_vec_test.py,
보드 자체를 점검하는 일은 scan7_selftest.py 가 이 파일을 불러서 합니다.

──────────────────────────────────────────────────────────────────────────────
 전체 구성
──────────────────────────────────────────────────────────────────────────────

  Jetson Nano                         7핀 스캔셀 보드                     칩 (SOP28)
  ───────────                         ──────────────                     ──────────
  SPI0 MOSI (GPIO10) ──> 셀1 ─> 셀2 ─> 셀3 ─> 셀4 ─> 셀5 ─> 셀6 ─> 셀7 ──> SPI0 MISO (GPIO9)
                          CK    MOSI   RST    SCK    SS    DONE   MISO
                          │      │      │      │      │      ↑      ↑
                       (바이패스) 구동  (바이패스) 구동   구동   캡처   캡처
                                 ↓             ↓      ↓      │      │
                               칩27번        칩25번  칩24번  칩23번  칩22번

  GPIO17 ─────────────────────────── 직결 ──────────────────────────> 칩 28번 CK  (i_clk)
  GPIO27 ─────────────────────────── 직결 ──────────────────────────> 칩 26번 RST (i_rstn)
  GPIO22 ──> 스캔셀 UPDATE  (shift FF 값을 update FF 로 옮겨 칩 핀에 반영)
  GPIO23 ──> 스캔셀 CAPTURE / SHIFT 선택 (74157 의 S 핀)

──────────────────────────────────────────────────────────────────────────────
 스캔셀 1개의 동작 (74157 mux + 74273 shift FF + 74273 update FF)
──────────────────────────────────────────────────────────────────────────────

  SHIFT 모드  : SPI SCLK 상승 에지마다  셀k ← 셀(k-1),  셀1 ← Jetson MOSI
  CAPTURE 모드: SPI SCLK 상승 에지에서   셀k ← 칩 핀 값  (칩 출력을 읽어 오는 동작)
  UPDATE 펄스 : update FF ← shift FF   (이때 비로소 칩 입력 핀 값이 바뀜)

  → SHIFT 중에는 update FF 가 그대로라서 칩 핀이 흔들리지 않는다 (글리치 없음).

──────────────────────────────────────────────────────────────────────────────
 SPI 1바이트 ↔ 셀 대응  (체인 7셀, SPI 8비트, MSB 먼저)
──────────────────────────────────────────────────────────────────────────────

  보내는 바이트(TX)  bit7  bit6  bit5  bit4  bit3  bit2  bit1  bit0
                     버림  셀7  셀6  셀5  셀4  셀3  셀2  셀1
  받는 바이트(RX)    셀7  셀6  셀5  셀4  셀3  셀2  셀1  (TX bit7 이 되돌아옴)

  - 8비트를 밀면 맨 먼저 나간 TX bit7 은 체인을 한 바퀴 돌아 RX bit0 으로 나온다.
  - 그래서 TX 는 "셀k → bit(k-1)", RX 는 "셀k → bit k" 로 한 칸 어긋난다.
  - 일반식 (체인 길이 N ≤ 8):  TX bit = k-1,   RX bit = k + 7 - N
  - 이 대응은 scan7_selftest.py 의 [2], [3] 단계에서 실측으로 확인한다.
"""

import time

# ─── GPIO 라이브러리 ────────────────────────────────────────────────────────
# Jetson Nano 에는 Jetson.GPIO 가 기본 설치되어 있다.
# 같은 코드를 라즈베리파이에서도 돌릴 수 있게 RPi.GPIO 로 대체 가능하게 해 둔다.
try:
    import Jetson.GPIO as GPIO
except ImportError:
    import RPi.GPIO as GPIO

# spidev: /dev/spidev0.0 을 여는 파이썬 모듈 (sudo pip3 install spidev)
import spidev


# ════════════════════════════════════════════════════════════════════════════
#  1. 설정 — 보드가 바뀌면 이 블록만 고친다
# ════════════════════════════════════════════════════════════════════════════

# ─── 제어 GPIO (BCM 번호 = SparkFun Pi Wedge 라벨 "G17" 등) ────────────────
PIN_CLK = 17        # 칩 CK (28번) 직결.  벡터 1개마다 0→1→0 펄스 1번
PIN_RST = 27        # 칩 RST (26번) 직결. Active-Low (0 = 리셋)
PIN_UPD = 22        # 스캔셀 UPDATE.      0→1→0 펄스로 shift FF → update FF
PIN_CAP = 23        # 스캔셀 CAPTURE/SHIFT 선택

# PIN_CAP 에 어떤 레벨을 줘야 "CAPTURE" 인가?
#   74157 의 S=0 이면 A 입력, S=1 이면 B 입력을 고른다.
#   - A 에 앞 셀 출력(시프트), B 에 칩 핀(캡처)을 연결했다면 CAPTURE = 1
#   - A 에 칩 핀(캡처),       B 에 앞 셀 출력(시프트)을 연결했다면 CAPTURE = 0
#   GPIO 표에 G23 이 "CAPTURE 모드" 로 적혀 있어 HIGH = CAPTURE 로 가정했다.
#   selftest [2] 체인 길이가 0 이나 엉뚱한 값이면 가장 먼저 이 값을 바꿔 본다.
CAP_LEVEL_CAPTURE = 1

# ─── 74HC14(반전 슈미트 버퍼) 를 홀수 번 거치는 신호는 True ─────────────────
# 신호가 74HC14 를 2번 거치면 반전이 상쇄되어 False, 1번만 거치면 True.
# True 로 두면 이 파일이 GPIO 에 "반대 레벨" 을 내보내서 칩/보드 쪽에서는 원래 값이 된다.
INVERT = {
    "CLK": False,   # GPIO17 → 칩 CK
    "RST": False,   # GPIO27 → 칩 RST
    "UPD": False,   # GPIO22 → 74273(update) CP
    "CAP": False,   # GPIO23 → 74157 S
}
SCAN_TX_INVERT = False   # Jetson MOSI → 셀1 경로가 반전되면 True (TX 바이트 전체 반전)
SCAN_RX_INVERT = False   # 셀7 → Jetson MISO 경로가 반전되면 True (RX 바이트 전체 반전)

# ─── SPI ────────────────────────────────────────────────────────────────────
SPI_BUS = 0              # /dev/spidev0.0  (헤더 19/21/23/24번 = Pi Wedge 의 MOSI/MISO/SCLK/CE0)
SPI_DEV = 0
SPI_SPEED_HZ = 500_000   # 브레드보드라면 500 kHz 로 시작. selftest 가 통과하면 1~2 MHz 로 올려 본다
SPI_MODE = 0             # CPOL=0, CPHA=0 : SCLK 평소 Low, 상승 에지에서 데이터 이동

# ─── 펄스 폭 / 대기 시간 ────────────────────────────────────────────────────
# Jetson.GPIO 는 한 번 바꾸는 데 이미 수십 µs 가 걸려서 74HC 계열에는 충분히 느리다.
# 오실로스코프로 볼 때나 배선이 길어 불안할 때만 키운다.
PULSE_S = 0              # UPDATE / CLK 펄스 High 유지 시간 (초). 0 = 추가 대기 없음
SETTLE_S = 0             # UPDATE 후, CLK 후 칩 출력이 안정될 때까지 기다리는 시간 (초)


# ════════════════════════════════════════════════════════════════════════════
#  2. 스캔체인 정의 — 셀 순서와 비트 위치
# ════════════════════════════════════════════════════════════════════════════

# 셀1 (Jetson MOSI 바로 다음) → 셀7 (Jetson MISO 로 나가는 마지막 셀)
CHAIN = ["CK", "MOSI", "RST", "SCK", "SS", "DONE", "MISO"]
N_CELLS = len(CHAIN)

# 각 셀의 역할
DRIVEN = ("MOSI", "SCK", "SS")     # 스캔셀이 칩 입력을 구동하는 셀
CAPTURED = ("DONE", "MISO")        # 칩 출력을 캡처하는 셀
BYPASS = ("CK", "RST")             # 셀은 있지만 칩에는 GPIO 가 직접 들어가는 셀 (값 무관)

# 칩 핀 번호 (보고서 출력용)
CHIP_PIN = {"CK": 28, "MOSI": 27, "RST": 26, "SCK": 25, "SS": 24, "DONE": 23, "MISO": 22}


def tx_bit(name):
    """셀 이름 → TX 바이트에서의 비트 위치.  셀k → bit(k-1)"""
    return CHAIN.index(name)                       # index 는 0부터 = k-1


def rx_bit(name):
    """셀 이름 → RX 바이트에서의 비트 위치.  셀k → bit(k + 7 - N)"""
    k = CHAIN.index(name) + 1
    return k + 7 - N_CELLS


# ════════════════════════════════════════════════════════════════════════════
#  3. 하드웨어 클래스
# ════════════════════════════════════════════════════════════════════════════

class Scan7:
    """7핀 스캔셀 보드 1개를 다루는 클래스.

    사용 예:
        with Scan7() as hw:
            rx = hw.shift(0x10)      # SS 셀에 1, 나머지 0 을 밀어넣고 직전 셀 값 받기
            hw.update()              # 칩 핀에 반영
            hw.clock()               # 칩 클럭 1번
            hw.capture()             # 칩 출력을 셀에 저장
            rx = hw.shift(0x00)      # 저장한 값 꺼내기
    """

    def __init__(self, speed_hz=None):
        # ── GPIO 초기화 ──
        GPIO.setmode(GPIO.BCM)     # 번호 체계 = BCM (Pi Wedge 라벨과 같은 번호)
        GPIO.setwarnings(False)    # 이전 실행에서 남은 설정 경고 끄기

        # 논리값(칩/보드 기준) 캐시. 같은 값을 또 쓰는 GPIO 호출을 생략해 속도를 아낀다.
        self._level = {}

        # 시작 상태: 칩 리셋 유지(RST=0), 클럭 Low, UPDATE Low, SHIFT 모드
        self._setup("CLK", PIN_CLK, 0)
        self._setup("RST", PIN_RST, 0)
        self._setup("UPD", PIN_UPD, 0)
        self._setup("CAP", PIN_CAP, 1 - CAP_LEVEL_CAPTURE)   # SHIFT 쪽 레벨

        # ── SPI 초기화 ──
        self.spi = spidev.SpiDev()
        self.spi.open(SPI_BUS, SPI_DEV)
        self.spi.max_speed_hz = speed_hz or SPI_SPEED_HZ
        self.spi.mode = SPI_MODE
        # 스캔체인은 CS 를 쓰지 않지만, spidev 가 CE0 을 토글해도 해가 없다.

    # ── 내부: GPIO 핀 설정과 출력 ────────────────────────────────────────────
    def _setup(self, name, pin, level):
        """핀을 출력으로 설정하고 초기 논리값을 건다 (반전 설정 반영)."""
        phys = level ^ (1 if INVERT[name] else 0)
        GPIO.setup(pin, GPIO.OUT, initial=phys)
        self._level[name] = level

    def _out(self, name, level):
        """논리값 level(0/1) 을 신호 name 에 출력. 이미 같은 값이면 아무것도 안 한다."""
        level = 1 if level else 0
        if self._level.get(name) == level:
            return
        pin = {"CLK": PIN_CLK, "RST": PIN_RST, "UPD": PIN_UPD, "CAP": PIN_CAP}[name]
        GPIO.output(pin, level ^ (1 if INVERT[name] else 0))
        self._level[name] = level

    @staticmethod
    def _wait(sec):
        if sec:
            time.sleep(sec)

    # ── 정리 ─────────────────────────────────────────────────────────────────
    def close(self):
        """칩을 리셋 상태로 두고 SPI/GPIO 를 반납한다."""
        try:
            self._out("CLK", 0)
            self._out("RST", 0)    # 끝나면 칩을 리셋에 묶어 둔다 (안전)
        finally:
            self.spi.close()
            GPIO.cleanup()         # 모든 핀을 입력으로 되돌림 → 보드 풀다운이 Low 유지

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── 스캔체인 기본 동작 ───────────────────────────────────────────────────
    def shift(self, tx):
        """SHIFT 모드에서 1바이트를 밀어넣고, 밀려 나온 1바이트를 돌려준다.

        tx : 셀에 넣을 값 (위 표의 TX 비트 배치)
        반환: 전송 직전에 셀에 들어 있던 값 (RX 비트 배치) — 보통 직전 CAPTURE 결과
        ※ UPDATE 를 하지 않으므로 칩 핀은 바뀌지 않는다.
        """
        self._out("CAP", 1 - CAP_LEVEL_CAPTURE)        # SHIFT 모드
        out = (tx ^ 0xFF) & 0xFF if SCAN_TX_INVERT else tx & 0xFF
        rx = self.spi.xfer2([out])[0]
        return (rx ^ 0xFF) & 0xFF if SCAN_RX_INVERT else rx

    def shift_bytes(self, tx_list):
        """여러 바이트를 연속으로 시프트 (selftest 의 체인 길이 측정용)."""
        self._out("CAP", 1 - CAP_LEVEL_CAPTURE)
        out = [((b ^ 0xFF) if SCAN_TX_INVERT else b) & 0xFF for b in tx_list]
        rx = self.spi.xfer2(out)
        return [((b ^ 0xFF) if SCAN_RX_INVERT else b) & 0xFF for b in rx]

    def update(self):
        """UPDATE 펄스: shift FF 값을 update FF 로 → 칩 입력 핀(SS/SCK/MOSI)이 바뀐다."""
        self._out("UPD", 1)
        self._wait(PULSE_S)
        self._out("UPD", 0)
        self._wait(SETTLE_S)

    def capture(self):
        """CAPTURE: 모든 셀이 칩 핀 값을 저장한다.

        CAPTURE 모드에서 SPI 1바이트(SCLK 8번)를 보낸다.
        매 에지마다 같은 핀 값을 다시 저장할 뿐이라 8번이어도 결과는 1번과 같다.
        끝나면 SHIFT 모드로 돌려놓는다.
        """
        self._out("CAP", CAP_LEVEL_CAPTURE)
        self.spi.xfer2([0x00])
        self._out("CAP", 1 - CAP_LEVEL_CAPTURE)

    # ── 직결 신호 ────────────────────────────────────────────────────────────
    def set_rst(self, rstn):
        """칩 RST(26번) 를 직접 설정. rstn=0 이면 리셋."""
        self._out("RST", rstn)

    def set_clk(self, level):
        """칩 CK(28번) 를 직접 설정 (selftest 용)."""
        self._out("CLK", level)

    def clock(self):
        """칩 클럭 1번: CK 0→1→0. 칩 내부 플립플롭이 상승 에지에서 1사이클 진행."""
        self._out("CLK", 1)
        self._wait(PULSE_S)
        self._out("CLK", 0)
        self._wait(SETTLE_S)
