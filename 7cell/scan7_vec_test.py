#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan7_vec_test.py  —  MyChip_FP_MAC 전체 테스트 벡터 재생 + 판정 (7핀 스캔셀 보드, Jetson Nano)

  sudo python3 scan7_vec_test.py                        # 전체 183,877 벡터, 17개 bin
  sudo python3 scan7_vec_test.py --bins 0,1             # bin 0, 1 만 (처음엔 이걸로)
  sudo python3 scan7_vec_test.py --bins 3-5 --chip c02  # bin 3~5, 칩 이름 c02
  sudo python3 scan7_vec_test.py --stop-on-fail         # 첫 FAIL bin 에서 멈춤
  sudo python3 scan7_vec_test.py --yield yield.csv      # 결과 한 줄을 yield.csv 에 추가

입력 파일 (이 폴더 → 현재 폴더 → ../vectors/conv_acc_v1 → ../testvector 순서로 찾음)
  vectors.bin    : WSL ~/test_vector/out/vectors.bin 그대로 (벡터당 1바이트)
  stim_bins.csv  : WSL ~/test_vector/vec/stim_bins.csv 그대로 (bin 목차)

출력
  화면           : bin 별 PASS / FAIL, 불일치 상세
  measured_<칩>.txt : 벡터마다 "<idx> <miso> <done>"  ← WSL 의 compare_log.py / decode_results.py 에 바로 사용

──────────────────────────────────────────────────────────────────────────────
 vectors.bin 1바이트 형식 (WSL vec/pack_for_fpga.py 가 만든 것)
──────────────────────────────────────────────────────────────────────────────
   bit7  MISO 마스크 (1 = 비교)      bit3  rstn  → GPIO27 직결
   bit6  MISO 기대값                 bit2  ssn   → 셀5 (SS)
   bit5  DONE 마스크 (1 = 비교)      bit1  sclk  → 셀4 (SCK)
   bit4  DONE 기대값                 bit0  mosi  → 셀2 (MOSI)

──────────────────────────────────────────────────────────────────────────────
 벡터 1개 = 칩 i_clk 1사이클.  원본 테스트벤치(sim/tb_vec.v)와 같은 순서로 재생한다
──────────────────────────────────────────────────────────────────────────────
   tb_vec.v                              이 스크립트
   ────────                              ──────────
   i_clk 하강 직후 입력 4개 인가    ←→   ① SHIFT(새 입력)  ② RST 직접 설정  ③ UPDATE
   i_clk 상승                       ←→   ④ CK 0→1→0
   다음 하강 직전 miso/done 샘플     ←→   ⑤ CAPTURE  → 다음 벡터의 SHIFT 에서 꺼냄

 파이프라인: SHIFT 한 번에 "새 벡터 넣기" 와 "직전 벡터 결과 꺼내기" 가 동시에 일어난다.
   SHIFT #0  TX=벡터0  RX=(무시)
   SHIFT #1  TX=벡터1  RX=벡터0 결과
   ...
   SHIFT #n  TX=더미   RX=벡터n-1 결과   ← bin 끝에서 한 번 더
"""
import argparse
import csv
import os
import sys
import time

import scan7_hw
from scan7_hw import Scan7, DRIVEN, CHIP_PIN, tx_bit, rx_bit

HERE = os.path.dirname(os.path.abspath(__file__))


# ════════════════════════════════════════════════════════════════════════════
#  1. 파일 읽기
# ════════════════════════════════════════════════════════════════════════════

def find_file(name, given=None):
    """입력 파일 위치 찾기. --vec / --bins-csv 로 직접 주면 그것을 쓴다."""
    if given:
        return given
    for d in (HERE, os.getcwd(),
              os.path.join(HERE, "..", "vectors", "conv_acc_v1"),   # 저장소 안의 벡터
              os.path.join(HERE, "..", "testvector")):              # Google Drive MyChips 폴더 구조
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    sys.exit("{} 를 찾을 수 없음. 이 폴더나 ../vectors/conv_acc_v1 에 두거나 경로를 직접 지정".format(name))


def load_bins(path):
    """stim_bins.csv → [(bin번호, 이름, 시작idx, 끝idx), ...]  (Windows CRLF 도 그대로 읽힘)"""
    with open(path, newline="") as f:
        return [(int(r["bin"]), r["name"], int(r["start"]), int(r["end"])) for r in csv.DictReader(f)]


def parse_bin_list(text, all_ids):
    """'0,1,5-7' → [0, 1, 5, 6, 7].  None 이면 전부."""
    if not text:
        return list(all_ids)
    out = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


# ════════════════════════════════════════════════════════════════════════════
#  2. 변환: vectors.bin 1바이트 → 스캔 TX 바이트 / RST 값 / 기대값 / 마스크
# ════════════════════════════════════════════════════════════════════════════

# 비트 위치는 scan7_hw 의 CHAIN 정의에서 계산한다 → 체인 순서가 바뀌면 거기만 고치면 된다
TX_SS, TX_SCK, TX_MOSI = tx_bit("SS"), tx_bit("SCK"), tx_bit("MOSI")
TX_DONE, TX_MISO = tx_bit("DONE"), tx_bit("MISO")
RX_DONE, RX_MISO = rx_bit("DONE"), rx_bit("MISO")


def convert(v):
    """vectors.bin 1바이트 v →
         tx    : 스캔체인에 넣을 바이트 (SS/SCK/MOSI 셀만 채움, 나머지 0)
         rstn  : GPIO27 로 직접 낼 리셋 값
         exp   : 기대 RX 값 (MISO, DONE 비트만)
         mask  : RX 에서 비교할 비트 (vectors.bin 마스크가 1인 것만)
    """
    rstn = (v >> 3) & 1
    ssn = (v >> 2) & 1
    sclk = (v >> 1) & 1
    mosi = v & 1
    tx = (ssn << TX_SS) | (sclk << TX_SCK) | (mosi << TX_MOSI)

    exp = mask = 0
    if v & 0x80:                                   # MISO 를 비교하는 벡터
        mask |= 1 << RX_MISO
        exp |= ((v >> 6) & 1) << RX_MISO
    if v & 0x20:                                   # DONE 을 비교하는 벡터
        mask |= 1 << RX_DONE
        exp |= ((v >> 4) & 1) << RX_DONE
    return tx, rstn, exp, mask


def output_mirror(v):
    """벡터 v 를 클럭한 뒤 칩이 내보낼 출력(기대값)을 출력 셀(DONE/MISO) TX 위치에 배치.

    출력 셀의 update FF 가 저항을 거쳐 칩 출력 핀을 '구동' 하는 구조라면,
    칩이 내는 값과 같은 값을 넣어 두어야 충돌 전류가 0 이 된다.
    출력 셀이 캡처만 하는 구조라면 이 값은 아무 영향이 없다 (그래서 기본으로 켜 둔다).
    """
    return (((v >> 6) & 1) << TX_MISO) | (((v >> 4) & 1) << TX_DONE)


# ════════════════════════════════════════════════════════════════════════════
#  3. bin 하나 실행
# ════════════════════════════════════════════════════════════════════════════

class BinResult:
    """bin 하나의 집계"""

    def __init__(self, bid, name, start, end):
        self.bid, self.name, self.start, self.end = bid, name, start, end
        self.n = 0              # 판정한 벡터 수
        self.miso_err = 0       # MISO 불일치 수
        self.done_err = 0       # DONE 불일치 수
        self.pin_err = 0        # 구동 핀 되읽기 불일치 수 (보드/접촉 문제)
        self.first = None       # 첫 불일치 벡터 번호
        self.details = []       # 출력할 불일치 상세

    @property
    def ok(self):
        return self.miso_err == 0 and self.done_err == 0

    def csv_cell(self):
        """compare_log.py 와 같은 표기: PASS 또는 FAIL@첫idx(miso=..;done=..)"""
        if self.ok:
            return "PASS"
        return "FAIL@{}(miso={};done={})".format(self.first, self.miso_err, self.done_err)


def run_bin(hw, data, br, args, log):
    """bin 하나 (data[start..end]) 를 재생하고 BinResult 를 채운다."""
    pending = None            # (idx, v, exp, mask, tx) : 결과를 아직 못 꺼낸 직전 벡터
    prev_v = None             # 직전 벡터 바이트 (출력 셀 미러용)

    def judge(p, rx):
        """직전 벡터 p 의 결과 rx 를 기대값과 비교하고 로그에 기록."""
        idx, v, exp, mask, tx = p
        br.n += 1
        miso = (rx >> RX_MISO) & 1
        done = (rx >> RX_DONE) & 1
        if log:
            log.write("{} {} {}\n".format(idx, miso, done))      # compare_log.py 형식

        diff = (rx ^ exp) & mask                                 # 비교 대상 비트 중 틀린 것
        bad_miso = (diff >> RX_MISO) & 1
        bad_done = (diff >> RX_DONE) & 1
        br.miso_err += bad_miso
        br.done_err += bad_done

        # 구동 핀 되읽기: SS/SCK/MOSI 셀은 방금 우리가 낸 값이 그대로 캡처되어야 한다
        pin_bad = [n for n in DRIVEN if ((rx >> rx_bit(n)) & 1) != ((tx >> tx_bit(n)) & 1)]
        if pin_bad:
            br.pin_err += 1

        if bad_miso or bad_done or pin_bad:
            if br.first is None and (bad_miso or bad_done):
                br.first = idx
            if len(br.details) < args.max_report:
                br.details.append(
                    "    idx {:>6}  in(rstn ssn sclk mosi)={:04b}  기대 miso={} done={}  "
                    "측정 miso={} done={}{}".format(
                        idx, v & 0xF,
                        (v >> 6) & 1 if v & 0x80 else "x", (v >> 4) & 1 if v & 0x20 else "x",
                        miso, done,
                        "  핀되읽기불일치=" + ",".join("{}({}번)".format(n, CHIP_PIN[n]) for n in pin_bad)
                        if pin_bad else ""))

    for i in range(br.start, br.end + 1):
        v = data[i]
        tx, rstn, exp, mask = convert(v)
        if args.follow and prev_v is not None:
            tx |= output_mirror(prev_v)        # 지금 칩 출력 핀에 걸린 값(직전 벡터 결과)과 같게

        rx = hw.shift(tx)                      # ① 새 입력 넣기 + 직전 벡터 결과 꺼내기
        if pending is not None:
            judge(pending, rx)

        hw.set_rst(rstn)                       # ② RST 직결 (바뀔 때만 실제 GPIO 호출)
        hw.update()                            # ③ SS/SCK/MOSI 를 칩 핀에 반영
        hw.clock()                             # ④ 칩 i_clk 1사이클
        hw.capture()                           # ⑤ MISO/DONE (+ 구동 핀) 을 셀에 저장

        pending = (i, v, exp, mask, tx)
        prev_v = v

        if args.progress and (i - br.start + 1) % args.progress == 0:
            print("      ... {} / {}".format(i - br.start + 1, br.end - br.start + 1), flush=True)

    # 마지막 벡터 결과 꺼내기 (UPDATE 를 하지 않으므로 칩 핀은 그대로)
    tail = output_mirror(prev_v) if (args.follow and prev_v is not None) else 0
    judge(pending, hw.shift(tail))


# ════════════════════════════════════════════════════════════════════════════
#  4. 메인
# ════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="MyChip_FP_MAC 테스트 벡터 재생 (7핀 스캔셀)")
    ap.add_argument("--vec", help="vectors.bin 경로")
    ap.add_argument("--bins-csv", help="stim_bins.csv 경로")
    ap.add_argument("--bins", help="실행할 bin (예: 0,1 / 3-5). 생략하면 전부")
    ap.add_argument("--chip", default="chip01", help="칩 이름 (로그 파일 이름과 yield.csv 에 사용)")
    ap.add_argument("--log", help="실측 로그 경로 (기본 measured_<칩>.txt, 'none' 이면 안 씀)")
    ap.add_argument("--yield", dest="yield_csv", help="결과 한 줄을 추가할 CSV 경로")
    ap.add_argument("--speed", type=int, help="스캔 SPI 속도 Hz")
    ap.add_argument("--stop-on-fail", action="store_true", help="첫 FAIL bin 에서 멈춤")
    ap.add_argument("--max-report", type=int, default=5, help="bin 당 출력할 불일치 상세 개수")
    ap.add_argument("--no-follow", dest="follow", action="store_false",
                    help="출력 셀에 기대 출력을 미러하지 않음 (출력 셀이 캡처 전용이면 결과 동일)")
    ap.add_argument("--progress", type=int, default=0, help="N 벡터마다 진행 표시 (0 = 안 함)")
    args = ap.parse_args()

    vec_path = find_file("vectors.bin", args.vec)
    bins_path = find_file("stim_bins.csv", args.bins_csv)
    with open(vec_path, "rb") as f:
        data = f.read()
    bins = load_bins(bins_path)
    assert bins[-1][3] + 1 == len(data), "vectors.bin 길이 {} 와 bin 끝 {} 이 다름".format(
        len(data), bins[-1][3] + 1)

    want = parse_bin_list(args.bins, [b[0] for b in bins])
    todo = [b for b in bins if b[0] in want]
    n_vec = sum(e - s + 1 for _, _, s, e in todo)

    log_path = None if (args.log or "").lower() == "none" else (args.log or "measured_{}.txt".format(args.chip))
    log = open(log_path, "w", newline="\n") if log_path else None

    print("#" * 78)
    print("# MyChip_FP_MAC  scan7 vector test   chip={}   bins={}   vectors={:,}".format(
        args.chip, ",".join(str(b[0]) for b in todo), n_vec))
    print("# vec={}  SPI={} Hz  follow={}  CAP_LEVEL_CAPTURE={}  INVERT={}".format(
        os.path.relpath(vec_path), args.speed or scan7_hw.SPI_SPEED_HZ, args.follow,
        scan7_hw.CAP_LEVEL_CAPTURE, {k: int(v) for k, v in scan7_hw.INVERT.items()}))
    print("#" * 78)

    results = []
    t0 = time.time()
    try:
        with Scan7(speed_hz=args.speed) as hw:
            for bid, name, s, e in todo:
                br = BinResult(bid, name, s, e)
                tb = time.time()
                run_bin(hw, data, br, args, log)
                results.append(br)
                dt = time.time() - tb
                print("bin {:>2} {:<26} {:>6} vec  {:>6.1f}s  {}{}".format(
                    bid, name, br.n, dt,
                    "PASS" if br.ok else "FAIL first@{} miso_err={} done_err={}".format(
                        br.first, br.miso_err, br.done_err),
                    "  (핀 되읽기 불일치 {}회 — 보드 확인)".format(br.pin_err) if br.pin_err else ""),
                    flush=True)
                for d in br.details:
                    print(d)
                if args.stop_on_fail and not br.ok:
                    print("--stop-on-fail: 여기서 멈춤")
                    break
    except KeyboardInterrupt:
        print("\n중단됨 (Ctrl+C) — 지금까지 결과만 출력")
    finally:
        if log:
            log.close()

    # ── 요약 ──
    total = time.time() - t0
    done_vec = sum(r.n for r in results)
    n_fail = sum(not r.ok for r in results)
    n_pin = sum(r.pin_err for r in results)
    print("-" * 78)
    print("벡터 {:,}개 / {:.1f}s ({:.0f} vec/s)   bin {}개 중 FAIL {}개{}".format(
        done_vec, total, done_vec / total if total else 0, len(results), n_fail,
        "   핀 되읽기 불일치 {}회".format(n_pin) if n_pin else ""))
    if log_path:
        print("실측 로그: {}  (WSL: python3 vec/compare_log.py out/expect_gls.txt {} vec/stim_bins.csv {})".format(
            log_path, os.path.basename(log_path), args.chip))

    row = [args.chip] + [r.csv_cell() for r in results]
    if args.yield_csv:
        with open(args.yield_csv, "a", newline="\n") as f:
            f.write(",".join(row) + "\n")
        print("yield 기록: {}".format(args.yield_csv))

    all_ok = n_fail == 0 and len(results) == len(todo)
    print("*** PASS ***" if all_ok else "*** FAIL ***")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
