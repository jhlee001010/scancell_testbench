# -*- coding: utf-8 -*-
"""
fake_board.py  —  PC 에서 하드웨어 없이 돌려 보기 위한 가짜 Jetson.GPIO / spidev + 가상 보드 + 가상 칩

목적
  scan7_hw.py / scan7_selftest.py / scan7_vec_test.py 를 "한 줄도 고치지 않고" PC 에서 실행해서
  - 비트 배치(TX/RX), 파이프라인(결과가 한 벡터 늦게 나오는 것), 판정 로직이 맞는지
  - 일부러 고장을 넣었을 때 FAIL 이 제대로 잡히는지
  를 확인한다.

모델링한 것
  ┌ 가상 Jetson ┐   GPIO17/27/22/23, SPI0 (MSB 먼저, mode 0)
  ├ 가상 보드   ┤   셀 7개 = shift FF + update FF + 74157 mux, 체인 CK→MOSI→RST→SCK→SS→DONE→MISO
  └ 가상 칩     ┘   "정답을 그대로 내는 칩":
                    클럭 상승마다 다음 벡터 번호로 넘어가고,
                    - 그 순간 칩 입력 핀(rstn/ssn/sclk/mosi)이 vectors.bin 의 입력과 같은지 검사 (TX 배치 검증)
                    - 출력 핀(miso/done)에 그 벡터의 기대값을 낸다                       (RX 배치 검증)
  실제 칩 논리(FP MAC)는 모델링하지 않는다. 그건 WSL 의 GLS 시뮬이 이미 검증했다.

고장 주입 (환경변수)
  FAKE_FAULT_MISO=idx  : 해당 벡터에서 칩 MISO 를 반대로 → 그 bin 이 FAIL 이어야 정상
  FAKE_FAULT_PIN=SCK   : 해당 구동 셀의 update FF → 소켓 연결 끊김(항상 0) → 핀 되읽기 불일치
  FAKE_NO_CHIP=1       : 소켓 비움 (출력 핀 = 0). selftest 용
"""
import os
import types

# scan7_hw 의 설정값(체인 순서, 핀 번호, 반전 등)은 bind() 로 나중에 받아 온다.
# (scan7_hw 는 import 하는 순간 Jetson.GPIO 를 찾으므로, 가짜 모듈을 먼저 설치한 뒤에 불러야 한다)
CHAIN = PIN_CLK = PIN_RST = PIN_UPD = PIN_CAP = CAP_LEVEL_CAPTURE = INVERT = None
TX_INV = RX_INV = RST_CLEARS = 0
CAPTURE_WIRED = ()


def bind(hw):
    """scan7_hw 모듈의 설정을 이 파일의 전역 변수로 복사하고 가상 보드를 새로 만든다.

    가상 보드는 scan7_hw 의 설정을 "실제 회로 사실" 로 받아들여 그대로 흉내 낸다:
      INVERT / SCAN_*_INVERT : 74HC14 1단 반전
      RST_CLEARS_SCAN        : RST 넷이 74273 /MR 에도 연결 → RST=0 이면 모든 셀 0
      CAPTURE_WIRED          : 74157 B 입력이 칩 핀에 연결된 셀 (나머지는 비어 있어 0 으로 읽힘)
    """
    global CHAIN, PIN_CLK, PIN_RST, PIN_UPD, PIN_CAP, CAP_LEVEL_CAPTURE, INVERT, BOARD
    global TX_INV, RX_INV, RST_CLEARS, CAPTURE_WIRED
    CHAIN, INVERT, CAP_LEVEL_CAPTURE = hw.CHAIN, hw.INVERT, hw.CAP_LEVEL_CAPTURE
    PIN_CLK, PIN_RST, PIN_UPD, PIN_CAP = hw.PIN_CLK, hw.PIN_RST, hw.PIN_UPD, hw.PIN_CAP
    TX_INV, RX_INV = int(hw.SCAN_TX_INVERT), int(hw.SCAN_RX_INVERT)
    RST_CLEARS, CAPTURE_WIRED = bool(hw.RST_CLEARS_SCAN), tuple(hw.CAPTURE_WIRED)
    BOARD = Board()
    return BOARD


# ════════════════════════════════════════════════════════════════════════════
#  가상 칩: 정답을 그대로 재생하는 모델
# ════════════════════════════════════════════════════════════════════════════

class ReplayChip:
    def __init__(self, data, order):
        self.data = data                  # vectors.bin 전체
        self.order = order                # 실행될 벡터 번호 순서 (선택한 bin 들을 이어 붙인 것)
        self.pos = -1                     # 지금까지 클럭된 벡터의 위치
        self.miso = 0                     # 출력 핀 값
        self.done = 0
        self.input_mismatch = []          # 클럭 순간 입력이 기대와 달랐던 벡터 번호
        self.fault_miso = int(os.environ.get("FAKE_FAULT_MISO", "-1"))

    def clock_rise(self, rstn, ssn, sclk, mosi):
        """CK 상승 에지: 다음 벡터로 넘어가며 입력 검사, 출력 갱신."""
        self.pos += 1
        if self.pos >= len(self.order):
            return
        idx = self.order[self.pos]
        v = self.data[idx]
        want = (v >> 3 & 1, v >> 2 & 1, v >> 1 & 1, v & 1)
        got = (rstn, ssn, sclk, mosi)
        # 리셋 중(rstn=0)에는 칩이 ssn/sclk/mosi 를 보지 않으므로 rstn 만 비교한다
        if (got != want) if want[0] else (rstn != 0):
            self.input_mismatch.append((idx, want, got))
        self.miso = (v >> 6) & 1
        self.done = (v >> 4) & 1
        if idx == self.fault_miso:
            self.miso ^= 1


# ════════════════════════════════════════════════════════════════════════════
#  가상 보드 (GPIO 레벨과 스캔셀 상태를 모두 들고 있음)
# ════════════════════════════════════════════════════════════════════════════

class Board:
    def __init__(self):
        self.gpio = {}                    # BCM 핀 → 물리 레벨
        self.shift_ff = [0] * len(CHAIN)  # 셀1..7 의 shift FF (index 0 = 셀1)
        self.upd_ff = [0] * len(CHAIN)    # 셀1..7 의 update FF
        self.chip = None                  # ReplayChip 또는 None (소켓 비움)
        self.broken_pin = os.environ.get("FAKE_FAULT_PIN")

    # 논리값 = 물리 레벨 ^ 반전 설정  (scan7_hw 가 반전해서 내보낸 것을 되돌림)
    def logic(self, name, pin):
        return self.gpio.get(pin, 0) ^ (1 if INVERT[name] else 0)

    def clear_if_reset(self):
        """RST 가 74273 /MR 에도 연결된 회로: RST=0 이면 모든 셀이 0 (비동기 클리어)."""
        if RST_CLEARS and self.logic("RST", PIN_RST) == 0:
            self.shift_ff = [0] * len(CHAIN)
            self.upd_ff = [0] * len(CHAIN)

    def capture_input(self, cell_name):
        """CAPTURE 때 셀이 읽는 값: 74157 B 가 칩 핀에 연결된 셀만 핀 값, 나머지는 빈 입력(0)."""
        return self.pin_value(cell_name) if cell_name in CAPTURE_WIRED else 0

    def pin_value(self, cell_name):
        """칩 핀 노드 값."""
        if cell_name == "CK":
            return self.logic("CLK", PIN_CLK)
        if cell_name == "RST":
            return self.logic("RST", PIN_RST)
        if cell_name in ("DONE", "MISO"):
            if self.chip is None:
                return 0                  # 소켓 비움 → 풀다운 가정
            return self.chip.done if cell_name == "DONE" else self.chip.miso
        # 구동 셀: update FF 출력이 그대로 핀에 걸림
        if cell_name == self.broken_pin:
            return 0
        return self.upd_ff[CHAIN.index(cell_name)]

    def on_gpio(self, pin, old, new):
        """GPIO 가 바뀔 때 보드 반응: RST 클리어, UPDATE 상승, CK 상승."""
        self.clear_if_reset()
        if pin == PIN_UPD and self.logic("UPD", pin) == 1 and (old ^ (1 if INVERT["UPD"] else 0)) == 0:
            self.upd_ff = list(self.shift_ff)                 # shift FF → update FF
            self.clear_if_reset()
        if pin == PIN_CLK and self.logic("CLK", pin) == 1 and (old ^ (1 if INVERT["CLK"] else 0)) == 0:
            if self.chip is not None:
                u = {n: self.pin_value(n) for n in ("SS", "SCK", "MOSI")}
                self.chip.clock_rise(self.logic("RST", PIN_RST), u["SS"], u["SCK"], u["MOSI"])

    def spi_byte(self, tx):
        """SPI 1바이트: 비트마다 (마스터가 셀7 값 샘플) → (SCLK 상승: 시프트 또는 캡처)."""
        capture = self.logic("CAP", PIN_CAP) == CAP_LEVEL_CAPTURE
        rx = 0
        for b in range(7, -1, -1):
            rx = (rx << 1) | (self.shift_ff[-1] ^ RX_INV)     # mode 0: 상승 에지 직전 값 샘플
            if capture:
                self.shift_ff = [self.capture_input(n) for n in CHAIN]
            else:
                bit = ((tx >> b) & 1) ^ TX_INV                # MOSI 경로 74HC14 반전
                self.shift_ff = [bit] + self.shift_ff[:-1]
            self.clear_if_reset()                             # /MR 이 Low 면 시프트해도 바로 지워짐
        return rx


BOARD = None      # bind() 가 만든다


# ════════════════════════════════════════════════════════════════════════════
#  가짜 Jetson.GPIO 모듈
# ════════════════════════════════════════════════════════════════════════════

def make_gpio_module():
    m = types.ModuleType("Jetson.GPIO")
    m.BCM, m.BOARD, m.OUT, m.IN, m.HIGH, m.LOW = "BCM", "BOARD", "OUT", "IN", 1, 0
    m.setmode = lambda mode: None
    m.setwarnings = lambda w: None

    def setup(pin, mode, initial=0):
        BOARD.gpio[pin] = initial

    def output(pin, value):
        old = BOARD.gpio.get(pin, 0)
        BOARD.gpio[pin] = 1 if value else 0
        BOARD.on_gpio(pin, old, BOARD.gpio[pin])

    m.setup, m.output = setup, output
    m.input = lambda pin: BOARD.gpio.get(pin, 0)
    m.cleanup = lambda *a: None
    return m


# ════════════════════════════════════════════════════════════════════════════
#  가짜 spidev 모듈
# ════════════════════════════════════════════════════════════════════════════

def make_spidev_module():
    m = types.ModuleType("spidev")

    class SpiDev(object):
        max_speed_hz = 0
        mode = 0

        def open(self, bus, dev):
            pass

        def close(self):
            pass

        def xfer2(self, data):
            return [BOARD.spi_byte(b) for b in data]

    m.SpiDev = SpiDev
    return m
