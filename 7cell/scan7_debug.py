#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan7_debug.py  —  selftest [1] 체인 길이가 None / 이상할 때 원인을 나눠 보는 진단 도구

  sudo python3 scan7_debug.py auto              # ① 설정을 바꿔 가며 체인 길이 자동 측정
  sudo python3 scan7_debug.py spi-loop          # ② Jetson SPI 자체 확인 (19번↔21번 점퍼 필요)
  sudo python3 scan7_debug.py hold 0x55         # ③ 패턴을 셀에 넣고 60초 유지 → 멀티미터로 74273 Q 핀 측정
  sudo python3 scan7_debug.py spi-run           # ④ SPI 를 10초 동안 계속 보냄 → SCLK/MOSI 가 칩까지 가는지 측정

증상이 "RX=00 00 00 00" (전부 0) 일 때 원인 후보와 가르는 법
  ┌──────────────────────────────┬──────────────────────────────────────────────┐
  │ 후보                         │ 가르는 단계                                   │
  ├──────────────────────────────┼──────────────────────────────────────────────┤
  │ CAPTURE 극성 반대             │ ① auto 에서 CAP 반대일 때 길이 7 이 나옴       │
  │   (SHIFT 인데 B=빈 핀을 읽음)  │                                              │
  │ Jetson SPI 꺼짐/불량          │ ② spi-loop 에서 보낸 값이 안 돌아옴            │
  │ 셀에 데이터가 안 들어감        │ ③ hold 에서 74273 Q 핀이 패턴과 다름           │
  │   (SCLK·MOSI·CP·/MR·전원)     │   + ④ spi-run 중 74273 CP(11번) 에 신호 있는지 │
  │ MISO 출력 경로 (마지막 Q→245)  │ ③ 에서 Q 는 맞는데 ① 은 여전히 None           │
  └──────────────────────────────┴──────────────────────────────────────────────┘
"""
import sys
import time

import scan7_hw as H
from scan7_hw import Scan7, CHAIN, N_CELLS, tx_bit

# 7셀 설계에서 각 셀의 shift 74273 Q 핀 (셀k → Q(k-1)).  보드가 다르면 참고만.
#   74273 핀: Q0=2, Q1=5, Q2=6, Q3=9, Q4=12, Q5=15, Q6=16, Q7=19
Q_PIN = [2, 5, 6, 9, 12, 15, 16]


def chain_length(hw, nbytes=4):
    """selftest [1] 과 같은 측정. (길이 또는 None, 받은 바이트들)"""
    hw.shift_bytes([0x00] * nbytes)
    rx = hw.shift_bytes([0xFF] * nbytes)
    for i in range(nbytes * 8):
        if (rx[i // 8] >> (7 - i % 8)) & 1:
            return i, rx
    return None, rx


def hexs(bs):
    return " ".join("%02X" % b for b in bs)


# ─── ① auto ─────────────────────────────────────────────────────────────────
def mode_auto():
    """CAPTURE 극성, 스캔 MOSI 반전을 바꿔 가며 체인 길이를 잰다.

    체인 길이 측정은 SHIFT 모드만 쓰므로, 영향을 주는 설정은
      - CAP 극성 (SHIFT 인데 실제로는 캡처 입력을 고르고 있으면 셀이 계속 0)
      - 스캔 MOSI 반전 (틀리면 길이 0 이 나옴)
    두 가지뿐이다. 4가지 조합을 모두 재서 7 이 나오는 조합을 찾는다.
    """
    orig = (H.CAP_LEVEL_CAPTURE, H.SCAN_TX_INVERT)
    found = []
    with Scan7() as hw:
        for cap in (orig[0], 1 - orig[0]):
            for txinv in (orig[1], not orig[1]):
                H.CAP_LEVEL_CAPTURE, H.SCAN_TX_INVERT = cap, txinv
                n, rx = chain_length(hw)
                mark = "  ← 지금 설정" if (cap, txinv) == orig else ""
                print("  CAP_LEVEL_CAPTURE={} SCAN_TX_INVERT={!s:<5}  길이 {!s:>4}   RX={}{}".format(
                    cap, txinv, n, hexs(rx), mark))
                if n == N_CELLS:
                    found.append((cap, txinv))
    H.CAP_LEVEL_CAPTURE, H.SCAN_TX_INVERT = orig
    print()
    if found and orig in found:
        print("지금 설정으로 길이 {} 정상. selftest 를 다시 돌려 보세요.".format(N_CELLS))
    elif found:
        cap, txinv = found[0]
        print("→ scan7_hw.py 를 이렇게 바꾸면 길이 {} 이 나옵니다:".format(N_CELLS))
        print("     CAP_LEVEL_CAPTURE = {}".format(cap))
        print("     SCAN_TX_INVERT = {}".format(txinv))
    else:
        print("어떤 조합도 길이 {} 이 아님 → 설정 문제가 아니라 배선/SPI 문제.".format(N_CELLS))
        print("  다음: spi-loop (Jetson SPI 확인) → hold (셀에 값이 들어가는지)")


# ─── ② spi-loop ─────────────────────────────────────────────────────────────
def mode_spi_loop():
    """보드를 거치지 않고 Jetson MOSI(19번)를 MISO(21번)에 직접 연결해 SPI 자체를 확인."""
    import spidev
    print("준비: 보드로 가는 MISO 선을 빼고, Jetson 헤더 19번(MOSI) ↔ 21번(MISO) 을 점퍼로 연결")
    s = spidev.SpiDev()
    s.open(H.SPI_BUS, H.SPI_DEV)
    s.max_speed_hz = H.SPI_SPEED_HZ
    s.mode = H.SPI_MODE
    tx = [0x5A, 0xA5, 0x00, 0xFF, 0x0F]
    rx = s.xfer2(list(tx))
    s.close()
    print("  보냄 {}".format(hexs(tx)))
    print("  받음 {}".format(hexs(rx)))
    if rx == tx:
        print("OK: Jetson SPI 정상 → 문제는 보드 쪽 (hold / spi-run 으로 계속)")
    elif all(b == 0 for b in rx):
        print("FAIL: 전부 0 → 점퍼 확인, 또는 SPI 가 꺼져 있음 (jetson-io 에서 spi1, /dev/spidev0.0)")
    elif all(b == 0xFF for b in rx):
        print("FAIL: 전부 FF → MISO 가 High 로 떠 있음 (점퍼가 21번에 안 꽂힘)")
    else:
        print("FAIL: 값이 다름 → 점퍼 접촉, 속도 (SPI_SPEED_HZ 를 100000 으로 낮춰 재시도)")


# ─── ③ hold ─────────────────────────────────────────────────────────────────
def mode_hold(pattern, sec):
    """패턴을 셀에 넣고 그대로 유지. SHIFT 만 하고 UPDATE 는 안 하므로 칩 핀은 안 바뀐다.

    멀티미터로 shift 74273 의 Q 핀을 재서, 셀에 의도한 값이 들어갔는지 본다.
    (반전 설정은 scan7_hw 가 처리하므로 아래 '기대' 는 셀 Q 핀의 실제 전압 레벨)
    """
    with Scan7() as hw:
        hw.shift(pattern)
        print("셀에 넣은 값 (TX 0x{:02X}) — shift 74273 Q 핀에서 측정:".format(pattern))
        print("   셀  신호   74273 핀   기대")
        for k, name in enumerate(CHAIN, 1):
            bit = (pattern >> tx_bit(name)) & 1
            print("   {:>2}  {:<5}  Q{} ({:>2}번)   {}".format(
                k, name, k - 1, Q_PIN[k - 1] if k <= len(Q_PIN) else "?", "5V (H)" if bit else "0V (L)"))
        print()
        print("함께 확인 (지금 상태):")
        print("   74157 ×2  1번(S)    : 0V  (SHIFT = A 입력)" if H.CAP_LEVEL_CAPTURE == 1 else
              "   74157 ×2  1번(S)    : 5V  (SHIFT)")
        print("   74157 ×2  15번(/E)  : 0V")
        print("   74273 ×2  1번(/MR)  : 5V")
        print("   74273 ×2  20번 VCC 5V, 10번 GND 0V")
        print("{}초 유지 중... (Ctrl+C 로 종료)".format(sec))
        try:
            time.sleep(sec)
        except KeyboardInterrupt:
            pass
    print()
    print("판정")
    print("  - Q 가 전부 0V        : SCLK 가 shift 74273 CP(11번)에 안 들어감, 또는 /MR=0V, 또는 S 극성 반대")
    print("  - Q 가 패턴과 반대     : 스캔 MOSI 반전 설정 (SCAN_TX_INVERT) 반대")
    print("  - Q 가 한 칸씩 밀림    : 셀 사이 연결 (앞 셀 Q → 다음 셀 mux A) 확인")
    print("  - Q 는 맞는데 RX 는 0  : 마지막 셀 Q → 74245 → Jetson 21번 경로 (245 DIR=5V, /OE=0V, 전원)")
    print("  다른 패턴도 해 보기: hold 0xAA, hold 0x00, hold 0xFF")


# ─── ④ spi-run ──────────────────────────────────────────────────────────────
def mode_spi_run(sec):
    """SPI 를 계속 보내서 SCLK/MOSI 가 보드까지 가는지 멀티미터/오실로스코프로 볼 수 있게 한다."""
    with Scan7() as hw:
        print("{}초 동안 SPI 0x55 를 계속 전송 (SHIFT 모드). 측정:".format(sec))
        print("  - shift 74273 11번(CP): 멀티미터 DC 로 약 절반 전압(1~3V) 이면 클럭이 들어오는 중")
        print("                          0V 또는 5V 로 고정이면 SCLK 경로 끊김")
        print("  - 셀1 74157 2번(1A, MOSI 입력): 마찬가지로 중간 전압이면 데이터가 들어오는 중")
        t0 = time.time()
        n = 0
        try:
            while time.time() - t0 < sec:
                hw.shift_bytes([0x55] * 64)
                n += 64
        except KeyboardInterrupt:
            pass
        print("  {} 바이트 전송 끝".format(n))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    m = sys.argv[1]
    if m == "auto":
        mode_auto()
    elif m == "spi-loop":
        mode_spi_loop()
    elif m == "hold":
        pat = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x55
        sec = float(sys.argv[3]) if len(sys.argv) > 3 else 60
        mode_hold(pat & 0xFF, sec)
    elif m == "spi-run":
        mode_spi_run(float(sys.argv[2]) if len(sys.argv) > 2 else 10)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
