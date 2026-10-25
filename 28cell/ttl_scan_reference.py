#!/usr/bin/env python3
"""TTL(74HC) 범용 28핀 스캔 테스터 - Jetson 단독 제어 (배선은 WIRING.md)

  Jetson SPI(MOSI/SCK/MISO) + GPIO(SE/UPDATE/CLR_n) -> 스캔 셀 32개 -> DUT 소켓 1~28번

셀 c (1~32) <-> SPI 4바이트 중 바이트 (c-1)//8 의 비트 7-(c-1)%8 (TX/RX 동일)
셀 1~28 = DUT 소켓 1~28번 핀, 셀 29~32 = 예비

1 벡터 = SHIFT(패턴) -> UPDATE(핀 인가) -> settle -> CAPTURE(핀 읽기) -> SHIFT(같은 패턴, 결과 unload)
"""
import time

import spidev

try:
    import Jetson.GPIO as GPIO
except ImportError:          # Raspberry Pi
    import RPi.GPIO as GPIO

# Jetson 40핀 BOARD 번호 (WIRING.md 6장)
PIN_SE = 29
PIN_UPDATE = 31
PIN_CLR_N = 33
PIN_DUT_CLK = 32           # 선택: DUT 클럭 핀에 점퍼로 연결했을 때만

NCELL = 32
NPIN = 28


class TTLScan:
    """하드웨어 레벨: 셀 32개 shift / update / capture"""

    def __init__(self, bus=0, dev=0, speed_hz=500_000, settle_s=20e-6, use_dut_clk=False):
        GPIO.setmode(GPIO.BOARD)
        GPIO.setwarnings(False)
        GPIO.setup(PIN_SE, GPIO.OUT, initial=GPIO.HIGH)
        GPIO.setup(PIN_UPDATE, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(PIN_CLR_N, GPIO.OUT, initial=GPIO.LOW)
        self.use_dut_clk = use_dut_clk
        if use_dut_clk:
            GPIO.setup(PIN_DUT_CLK, GPIO.OUT, initial=GPIO.LOW)
        self.spi = spidev.SpiDev()
        self.spi.open(bus, dev)
        self.spi.max_speed_hz = speed_hz
        self.spi.mode = 0
        self.settle_s = settle_s
        self.reset()

    def close(self):
        self.spi.close()
        GPIO.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ---------- 비트 변환 ----------
    @staticmethod
    def pack(cells):
        """cells[1..32] (0/1) -> 4바이트"""
        b = [0, 0, 0, 0]
        for c in range(1, NCELL + 1):
            if cells[c]:
                b[(c - 1) // 8] |= 0x80 >> ((c - 1) % 8)
        return b

    @staticmethod
    def unpack(data):
        """4바이트 -> cells[1..32] (cells[0] 미사용)"""
        return [0] + [(data[(c - 1) // 8] >> (7 - (c - 1) % 8)) & 1 for c in range(1, NCELL + 1)]

    # ---------- 기본 동작 ----------
    def reset(self):
        """CLR_n 펄스: shift/update FF 전부 0 -> DUT 핀 전부 0 구동"""
        GPIO.output(PIN_CLR_N, GPIO.LOW)
        time.sleep(1e-3)
        GPIO.output(PIN_CLR_N, GPIO.HIGH)

    def shift(self, cells):
        """SE=1, 32비트 shift. 셀 내용 교체 후 이전 셀 내용 반환"""
        GPIO.output(PIN_SE, GPIO.HIGH)
        return self.unpack(self.spi.xfer2(self.pack(cells)))

    def update(self):
        GPIO.output(PIN_UPDATE, GPIO.HIGH)
        GPIO.output(PIN_UPDATE, GPIO.LOW)

    def capture(self):
        """SE=0 에서 SCK 8번 -> 셀 1~28 이 DUT 핀 값을 캡처 (같은 값을 반복 저장하므로 무해)"""
        GPIO.output(PIN_SE, GPIO.LOW)
        self.spi.xfer2([0])
        GPIO.output(PIN_SE, GPIO.HIGH)

    def dut_clock(self, n=1):
        if not self.use_dut_clk:
            raise RuntimeError("use_dut_clk=True 로 열고 DUT 클럭 핀에 DCLK 점퍼 연결 필요")
        for _ in range(n):
            GPIO.output(PIN_DUT_CLK, GPIO.HIGH)
            GPIO.output(PIN_DUT_CLK, GPIO.LOW)

    def apply(self, drive, clock=0):
        """drive[1..28] 를 핀에 인가 (clock>0 이면 GPIO DUT 클럭 n번) 후 핀 값 [1..28] 반환"""
        cells = [0] + [drive[p] & 1 for p in range(1, NPIN + 1)] + [0] * (NCELL - NPIN)
        self.shift(cells)
        self.update()
        if clock:
            self.dut_clock(clock)
        time.sleep(self.settle_s)
        self.capture()
        return self.shift(cells)[:NPIN + 1]          # UPDATE 없음 -> 핀 유지

    def chain_length(self, max_bytes=8):
        """0 으로 채운 뒤 1 을 밀어넣어 처음 1 이 나오는 위치 = 체인 길이"""
        GPIO.output(PIN_SE, GPIO.HIGH)
        self.spi.xfer2([0x00] * max_bytes)
        rx = self.spi.xfer2([0xFF] * max_bytes)
        for i in range(max_bytes * 8):
            if (rx[i // 8] >> (7 - i % 8)) & 1:
                return i
        return None


class DUT:
    """핀맵 레벨: 핀 이름으로 입력 인가 / 출력 읽기

    pinmap = {소켓핀번호: ("이름", 역할)},  역할 = "in" | "out" | "vcc" | "gnd" | "nc"
      in  : 셀이 구동 (readback 으로 단락/접촉 불량 검사)
      out : DUT 가 구동. 셀은 직전 캡처값을 따라가 1kΩ 충돌 전류 최소화
      vcc/gnd : 점퍼로 전원 연결된 핀. 셀을 같은 레벨로 구동해 충돌 0
      nc  : 미사용 (0 구동)
    """

    def __init__(self, ts: TTLScan, pinmap):
        self.ts = ts
        self.pinmap = pinmap
        self.by_name = {name: (pin, role) for pin, (name, role) in pinmap.items()}
        self.drive = [0] * (NPIN + 1)
        for pin, (name, role) in pinmap.items():
            if role == "vcc":
                self.drive[pin] = 1
        self.errors = []

    def apply(self, clock=0, **inputs):
        """입력 이름=값 인가 -> {출력이름: 값} 반환. 지정 안 한 입력은 이전 값 유지"""
        for name, v in inputs.items():
            pin, role = self.by_name[name]
            if role != "in":
                raise ValueError(f"{name}({pin}번) 은 입력 핀이 아님")
            self.drive[pin] = 1 if v else 0
        pins = self.ts.apply(self.drive, clock)
        out = {}
        for pin, (name, role) in self.pinmap.items():
            if role == "out":
                out[name] = pins[pin]
                self.drive[pin] = pins[pin]               # 충돌 최소화: 다음엔 같은 값으로 구동
            elif role in ("in", "vcc", "gnd") and pins[pin] != self.drive[pin]:
                self.errors.append(f"{name}({pin}번) 구동 {self.drive[pin]} 인데 {pins[pin]} 읽힘 (단락/점퍼/접촉 확인)")
        return out

    def bus(self, out, names):
        """출력 dict 에서 names[0]=LSB 순서로 정수 조립"""
        return sum(out[n] << i for i, n in enumerate(names))

    def set_bus(self, names, value):
        return {n: (value >> i) & 1 for i, n in enumerate(names)}


def dip16_socket(dip_pin):
    """DIP16 칩을 28핀 소켓 위쪽에 맞춰 꽂았을 때 소켓 핀 번호"""
    return dip_pin if dip_pin <= 8 else dip_pin + 12
