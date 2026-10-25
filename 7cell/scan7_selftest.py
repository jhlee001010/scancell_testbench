#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan7_selftest.py  —  7핀 스캔셀 보드 자체 점검 (칩 테스트 전에 반드시 먼저)

  sudo python3 scan7_selftest.py            # 소켓 비운 상태로 [1]~[4]
  sudo python3 scan7_selftest.py --chip     # 칩을 꽂은 상태로 [1]~[5] (리셋 직후 출력까지 확인)
  sudo python3 scan7_selftest.py --speed 1000000   # SPI 1 MHz 로 점검

점검 순서와 의미
  [1] 체인 길이     : 0 으로 채운 뒤 1 을 밀어 처음 1 이 나오는 위치 = 셀 개수 (7 이어야 함)
                      → Jetson MOSI/SCLK/MISO, 셀 사이 연결, CAPTURE 극성 확인
  [2] 비트 배치     : 패턴 p 를 넣고 다음 전송에서 받은 값이 (p << 1) 인지
                      → scan7_hw.py 의 TX/RX 비트 대응표가 실제 보드와 같은지 확인
  [3] 구동/캡처     : SS/SCK/MOSI 셀에 walking-1, walking-0 → UPDATE → CAPTURE → 되읽기
                      → update FF, mux 의 캡처 입력, UPDATE/CAPTURE 신호 확인
  [4] 직결 신호     : GPIO17(CK), GPIO27(RST) 를 바꾸고 CK/RST 셀로 캡처
                      → 바이패스 셀이 칩 핀을 읽는 구조라면 직결선까지 확인 (아니면 참고용)
  [5] 칩 리셋 출력  : (--chip) 리셋 중 SS=1 상태에서 DONE=0, MISO=0 인지

모든 단계가 OK 여야 scan7_vec_test.py 로 넘어간다.
"""
import argparse
import random

from scan7_hw import Scan7, N_CELLS, DRIVEN, BYPASS, CHIP_PIN, tx_bit, rx_bit


def bits(b):
    """바이트를 '0101 1010' 처럼 읽기 쉬운 문자열로."""
    s = format(b & 0xFF, "08b")
    return s[:4] + " " + s[4:]


def chain_length(hw, nbytes=4):
    """[1] 체인 길이 측정.

    1) 0x00 을 nbytes 바이트 밀어 모든 셀을 0 으로 채운다.
    2) 0xFF 를 nbytes 바이트 밀면서 받은 비트열을 앞에서부터 본다.
       처음 N 비트는 셀에 있던 0 이고, 그 뒤부터 방금 넣은 1 이 나온다.
       → 처음 1 이 나온 위치 = N (셀 개수)
    반환: N, 또는 1 이 끝까지 안 나오면 None
    """
    hw.shift_bytes([0x00] * nbytes)
    rx = hw.shift_bytes([0xFF] * nbytes)
    for i in range(nbytes * 8):
        if (rx[i // 8] >> (7 - i % 8)) & 1:       # MSB 먼저 받은 순서대로 본다
            return i, rx
    return None, rx


def main():
    ap = argparse.ArgumentParser(description="7핀 스캔셀 보드 자체 점검")
    ap.add_argument("--chip", action="store_true", help="칩을 꽂은 상태 ([5] 리셋 출력 확인 추가)")
    ap.add_argument("--speed", type=int, default=None, help="SPI 속도 Hz (기본: scan7_hw.SPI_SPEED_HZ)")
    a = ap.parse_args()

    fails = 0
    with Scan7(speed_hz=a.speed) as hw:
        hw.set_rst(0)                 # 점검 내내 칩은 리셋 상태로 둔다 (칩이 있어도 안전)

        # ─────────────────────────────────────────────────────────────────
        print("[1] 체인 길이")
        n, rx = chain_length(hw)
        ok = (n == N_CELLS)
        fails += not ok
        print("    측정 {} / 기대 {}   RX={}   {}".format(
            n, N_CELLS, " ".join("%02X" % b for b in rx), "OK" if ok else "FAIL"))
        if not ok:
            print("    - None(1 이 안 나옴): Jetson MISO 선, 셀7 출력, 또는 CAP_LEVEL_CAPTURE 반대")
            print("    - 0                : MISO 가 High 로 붙어 있음 (단락/레벨시프터 방향)")
            print("    - 7 이 아닌 숫자    : 셀 사이 연결이 빠지거나 겹침 (mux B ← 앞 셀 Q)")
            print("    체인이 맞지 않으면 다음 단계는 의미가 없어 여기서 멈춘다.")
            return report(fails)

        # ─────────────────────────────────────────────────────────────────
        print("[2] 비트 배치 (TX 셀k→bit k-1, RX 셀k→bit k+7-N)")
        shift_l = 8 - N_CELLS                       # N=7 이면 1칸 왼쪽으로 어긋나서 나온다
        pats = [1 << i for i in range(8)] + [0x55, 0xAA, 0x0F, 0xF0] + \
               [random.getrandbits(8) for _ in range(8)]
        bad = 0
        for p in pats:
            hw.shift(p)                             # 패턴 넣기
            got = hw.shift(0x00)                    # 다음 전송에서 꺼내기
            exp = (p << shift_l) & 0xFF             # 맨 앞 비트는 체인 밖으로 밀려 나가 사라짐
            if got != exp:
                bad += 1
                print("    FAIL TX {}  RX {}  (기대 {})".format(bits(p), bits(got), bits(exp)))
        fails += bad > 0
        print("    {}개 패턴 중 {}개 불일치  {}".format(len(pats), bad, "OK" if not bad else "FAIL"))

        # ─────────────────────────────────────────────────────────────────
        print("[3] 구동 → 캡처 되읽기 (셀: {})".format(", ".join(DRIVEN)))
        # walking-1 : 구동 셀 하나만 1,  walking-0 : 하나만 0
        vecs = []
        for name in DRIVEN:
            vecs.append({n_: int(n_ == name) for n_ in DRIVEN})
            vecs.append({n_: int(n_ != name) for n_ in DRIVEN})
        vecs.append({n_: 0 for n_ in DRIVEN})
        vecs.append({n_: 1 for n_ in DRIVEN})
        bad_cells = set()
        for v in vecs:
            tx = 0
            for name, val in v.items():
                tx |= val << tx_bit(name)           # 셀 위치에 값 배치
            hw.shift(tx)                            # 셀에 넣고
            hw.update()                             # 칩 핀(소켓)에 반영
            hw.capture()                            # 핀 값을 다시 셀로 읽어 들이고
            rx = hw.shift(0x00)                     # 꺼낸다
            for name, val in v.items():
                got = (rx >> rx_bit(name)) & 1
                if got != val:
                    bad_cells.add(name)
                    print("    FAIL {:<4}(칩 {}번) 구동 {} → 캡처 {}".format(name, CHIP_PIN[name], val, got))
        fails += bool(bad_cells)
        print("    {}".format("OK" if not bad_cells else "FAIL 셀: " + ", ".join(sorted(bad_cells))))
        if bad_cells:
            print("    - 모든 셀 FAIL: UPDATE(GPIO22) 또는 CAPTURE(GPIO23) 신호/극성 확인")
            print("    - 일부 셀 FAIL: 그 셀의 update FF 출력 → 소켓 핀 → mux 캡처 입력 배선 확인")

        # ─────────────────────────────────────────────────────────────────
        print("[4] 직결 신호 (GPIO17 → CK 28번, GPIO27 → RST 26번)  ※ 참고용")
        # RST 를 1 로 올리는 순간 칩이 리셋에서 풀린다. 칩이 있어도 CK 를 치지 않으므로 상태 변화 없음.
        for name, setter in (("CK", hw.set_clk), ("RST", hw.set_rst)):
            seen = []
            for level in (0, 1, 0):
                setter(level)
                hw.capture()
                rx = hw.shift(0x00)
                seen.append((level, (rx >> rx_bit(name)) & 1))
            ok4 = all(l == g for l, g in seen)
            print("    {:<3} 출력/캡처 {}  {}".format(
                name, " ".join("{}/{}".format(l, g) for l, g in seen),
                "OK" if ok4 else "불일치 (바이패스 셀이 핀을 안 읽는 구조면 정상 — 멀티미터로 확인)"))
        hw.set_clk(0)
        hw.set_rst(0)

        # ─────────────────────────────────────────────────────────────────
        if a.chip:
            print("[5] 칩 리셋 중 출력 (RST=0, SS=1)")
            hw.shift(1 << tx_bit("SS"))             # SS=1 (비선택), SCK=0, MOSI=0
            hw.update()
            hw.clock()                              # 리셋 중 클럭 1번 (벡터 0 과 같은 상황)
            hw.capture()
            rx = hw.shift(0x00)
            done = (rx >> rx_bit("DONE")) & 1
            miso = (rx >> rx_bit("MISO")) & 1
            ok5 = (done == 0 and miso == 0)
            fails += not ok5
            print("    DONE={} MISO={}  (기대 0 0)  {}".format(done, miso, "OK" if ok5 else "FAIL"))
            if not ok5:
                print("    - 칩 전원/GND, 칩 방향(1번 핀 점), 23·22번 핀 → 셀6·7 캡처 입력 확인")

    return report(fails)


def report(fails):
    print("*** SELFTEST PASS ***" if fails == 0 else "*** SELFTEST FAIL ({}) ***".format(fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
