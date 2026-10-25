# scancell_testbench

74HC 계열 로직으로 만든 **스캔셀(scan cell) 보드**와 **Jetson Nano** 로
MyChip MPW 칩을 실측 테스트하는 테스트벤치 모음입니다.

WSL 에서 게이트 넷리스트 시뮬레이션(GLS)으로 만든 **"클럭마다의 정답" (`vectors.bin`)** 을
스캔체인으로 칩 핀에 그대로 재생하고, 칩 출력을 캡처해서 bin 별 PASS/FAIL 을 판정합니다.

```
 WSL test_vector (GLS)          Jetson Nano                  스캔셀 보드              칩
 ─────────────────────          ───────────                  ──────────             ───
 vectors.bin ─────────────────> scan7_vec_test.py ──SPI────> 셀 N 개 ─── 핀 ──────> DUT
 (입력 + 기대값 + 마스크)        GPIO (CLK/RST/UPD/CAP) ───────────── 직결 ────────> CLK/RST
                                     │ <──────────────────── 캡처 ───────────────── 출력 핀
                                     └─> bin 별 PASS/FAIL, measured_<칩>.txt
```

---

## 폴더 구성

```
scancell_testbench/
├─ 7cell/                 7핀 스캔셀 — Convolution Accelerator v1 (MyChip_FP_MAC) 전용  ✅ 사용 가능
│  ├─ scan7_hw.py         보드 제어 계층 (설정은 여기만)
│  ├─ scan7_selftest.py   보드 자가진단
│  ├─ scan7_vec_test.py   전체 벡터 재생 + 판정
│  ├─ dryrun/             PC 에서 하드웨어 없이 검증
│  └─ README.md           사용법
├─ 28cell/                28핀 범용 스캔셀 (SOP28 ZIF)  🚧 예정
│  └─ README.md           계획 + 기존 ttl_scan 설계 참고 자료
├─ vectors/
│  └─ conv_acc_v1/        Convolution Accelerator v1 테스트 벡터
│     ├─ vectors.bin      183,877 벡터 (1바이트/벡터)
│     └─ stim_bins.csv    17개 bin 목차
└─ docs/
   ├─ 7cell_vector_conversion.md   변환 규칙·핀맵·비트 배치·타이밍·주의사항 상세 정리
   ├─ 7cell_circuit_review.md      7pin_claude.circ 핀 단위 점검 결과 + 회로 수정안
   └─ conv_acc_pinmap_v1.pdf       칩 SOP28 핀맵
```

---

## 빠른 시작 (7cell)

```bash
git clone https://github.com/jhlee001010/scancell_testbench.git
```

```bash
cd scancell_testbench/7cell
```

PC 에서 하드웨어 없이 먼저 확인:

```bash
python3 dryrun/run_dryrun.py vec --bins 0,1
```

Jetson Nano 에서 (반드시 `python3`, GPIO 권한 때문에 `sudo`):

```bash
sudo python3 scan7_selftest.py
```

```bash
sudo python3 scan7_vec_test.py --bins 0,1
```

```bash
sudo python3 scan7_vec_test.py --chip chip01 --yield yield.csv --progress 20000
```

자세한 내용은 [`7cell/README.md`](7cell/README.md).

---

## 테스트 벡터 갱신

벡터는 WSL 의 `test_vector` 저장소에서 만듭니다 (`run_all.sh` → `out/vectors.bin`).
칩 설계나 벡터 생성기가 바뀌면 다시 만들어 이 저장소로 복사합니다.

```bash
cd ~/test_vector && ./run_all.sh
```

```bash
cp ~/test_vector/out/vectors.bin ~/test_vector/vec/stim_bins.csv ~/scancell_testbench/vectors/conv_acc_v1/
```

실측 로그(`measured_<칩>.txt`)는 `test_vector` 의 도구로 다시 판정·해석할 수 있습니다.

```bash
python3 vec/compare_log.py out/expect_gls.txt measured_chip01.txt vec/stim_bins.csv chip01
```

---

## 칩이 늘어날 때

| 추가할 것 | 위치 |
|---|---|
| 새 칩의 테스트 벡터 | `vectors/<칩이름>/vectors.bin`, `stim_bins.csv` |
| 새 칩의 핀맵 / 정리 문서 | `docs/` |
| 같은 7셀 보드로 다른 칩 | `7cell/` 의 `CHAIN` 순서와 `convert()` 만 바꾼 스크립트 |
| 28핀 범용 보드 | `28cell/` (계획은 [`28cell/README.md`](28cell/README.md)) |
