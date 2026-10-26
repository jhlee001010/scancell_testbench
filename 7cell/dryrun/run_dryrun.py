#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_dryrun.py  —  젯슨/보드/칩 없이 PC 에서 테스트벤치 전체 흐름을 돌려 보기

  python dryrun/run_dryrun.py selftest                 # 보드 자가진단 (소켓 비움)
  python dryrun/run_dryrun.py selftest --chip          # 칩 꽂은 상태 자가진단
  python dryrun/run_dryrun.py vec --bins 0,1           # 벡터 재생 (bin 0, 1)
  python dryrun/run_dryrun.py vec                      # 전체 183,877 벡터 (PC 에서 수십 초)
  python dryrun/run_dryrun.py debug auto               # 진단 도구 (scan7_debug.py) 시험

  고장 주입 예 (PowerShell 은 $env:FAKE_FAULT_MISO=60 ; 실행 ; Remove-Item Env:FAKE_FAULT_MISO)
    FAKE_FAULT_MISO=60   python dryrun/run_dryrun.py vec --bins 0,1   → bin 1 FAIL first@60
    FAKE_FAULT_PIN=SCK   python dryrun/run_dryrun.py selftest         → [3] SCK FAIL

동작
  1) 가짜 Jetson.GPIO / spidev 모듈을 sys.modules 에 넣는다 (진짜 모듈 대신 쓰이게)
  2) scan7_hw 를 불러오고, 그 설정(체인 순서/핀/반전)으로 가상 보드를 만든다
  3) vec 모드면 선택한 bin 순서대로 "정답을 내는 가상 칩" 을 소켓에 꽂는다
  4) 진짜 scan7_selftest.py / scan7_vec_test.py 의 main() 을 그대로 실행한다
  5) vec 모드면 가상 칩이 본 입력(rstn/ssn/sclk/mosi)이 vectors.bin 과 모두 같았는지 추가 확인
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                     # scan7_testbench 폴더
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import fake_board                                # noqa: E402

# ── 1) 가짜 모듈 설치: "import Jetson.GPIO as GPIO" 와 "import spidev" 가 이것을 받는다 ──
gpio = fake_board.make_gpio_module()
jetson_pkg = types.ModuleType("Jetson")
jetson_pkg.GPIO = gpio
sys.modules["Jetson"] = jetson_pkg
sys.modules["Jetson.GPIO"] = gpio
sys.modules["spidev"] = fake_board.make_spidev_module()

# ── 2) 진짜 하드웨어 계층을 불러와 가상 보드에 연결 ──
import scan7_hw                                  # noqa: E402
board = fake_board.bind(scan7_hw)

if len(sys.argv) < 2 or sys.argv[1] not in ("selftest", "vec", "debug"):
    sys.exit(__doc__)
mode = sys.argv[1]
rest = sys.argv[2:]
no_chip = os.environ.get("FAKE_NO_CHIP") == "1"

if mode == "debug":
    import scan7_debug
    sys.argv = ["scan7_debug.py"] + rest
    sys.exit(scan7_debug.main())

if mode == "selftest":
    import scan7_selftest
    if "--chip" in rest and not no_chip:
        # 자가진단 [5] 는 리셋 중 클럭 1번 후 DONE=0, MISO=0 을 본다 = 벡터 0 의 상황
        board.chip = fake_board.ReplayChip(b"\xa4", [0] * 1000)
    sys.argv = ["scan7_selftest.py"] + rest
    sys.exit(scan7_selftest.main())

# ── vec 모드 ──
import scan7_vec_test                            # noqa: E402

# 선택한 bin 들을 실행 순서대로 이어 붙인 벡터 번호 목록 (가상 칩이 이 순서로 정답을 낸다)
bins_arg = rest[rest.index("--bins") + 1] if "--bins" in rest else None
data = open(scan7_vec_test.find_file("vectors.bin"), "rb").read()
bins = scan7_vec_test.load_bins(scan7_vec_test.find_file("stim_bins.csv"))
want = scan7_vec_test.parse_bin_list(bins_arg, [b[0] for b in bins])
order = [i for bid, _, s, e in bins if bid in want for i in range(s, e + 1)]
chip = None if no_chip else fake_board.ReplayChip(data, order)
board.chip = chip

if "--log" not in rest:
    rest += ["--log", os.path.join(HERE, "measured_dryrun.txt")]
sys.argv = ["scan7_vec_test.py", "--chip", "dryrun"] + rest
rc = scan7_vec_test.main()

if chip is not None:
    print("[dryrun] 가상 칩이 받은 클럭 {} / 기대 {}  {}".format(
        chip.pos + 1, len(order), "OK" if chip.pos + 1 == len(order) else "불일치"))
    print("[dryrun] 클럭 순간 입력 불일치 {}개 {}".format(
        len(chip.input_mismatch), "OK" if not chip.input_mismatch else chip.input_mismatch[:5]))
    if chip.input_mismatch or chip.pos + 1 != len(order):
        rc = 1
sys.exit(rc)
