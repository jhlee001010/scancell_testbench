# scan7_testbench — 7핀 스캔셀로 MyChip_FP_MAC 테스트하기 (Jetson Nano)

WSL `~/test_vector` 에서 만든 **테스트 벡터 183,877개(`vectors.bin`)** 를
**7핀 스캔셀 보드 + Jetson Nano** 로 실제 칩에 재생하고, bin 별 PASS/FAIL 을 판정하는 테스트벤치입니다.

> 변환 규칙, 핀맵, 비트 배치의 자세한 배경은
> [`docs/7cell_vector_conversion.md`](../docs/7cell_vector_conversion.md) 에 있습니다.

---

## 1. 폴더 구성

```
7cell/
├─ scan7_hw.py          보드 제어 계층 (GPIO/SPI, shift·update·capture·clock)  ← 설정은 여기만 고침
├─ scan7_selftest.py    보드 자가진단 (칩 꽂기 전에 먼저)
├─ scan7_vec_test.py    전체 벡터 재생 + bin 별 판정 + 실측 로그
├─ README.md            이 문서
└─ dryrun/              PC 에서 하드웨어 없이 돌려 보기
   ├─ fake_board.py     가짜 Jetson.GPIO / spidev + 가상 스캔셀 보드 + "정답을 내는" 가상 칩
   └─ run_dryrun.py     위 가짜 모듈을 끼우고 진짜 스크립트를 그대로 실행
```

입력 파일 `vectors.bin`, `stim_bins.csv` 는 **이 폴더 → 현재 폴더 → `../vectors/conv_acc_v1` → `../testvector`** 순서로 자동으로 찾습니다.
저장소에 `vectors/conv_acc_v1/` 이 같이 들어 있어서, 클론만 하면 따로 복사할 필요가 없습니다.

---

## 2. 하드웨어 연결 (BCM 번호 = Pi Wedge 라벨)

| Jetson | 용도 | 연결 |
|---|---|---|
| GPIO17 (G17) | 칩 CK | 칩 28번 **직결** (스캔셀 바이패스) |
| GPIO27 (G27) | 칩 RST (Active-Low) | 칩 26번 **직결** (스캔셀 바이패스) |
| GPIO22 (G22) | 스캔셀 UPDATE | 74273(update) CP |
| GPIO23 (G23) | 스캔셀 CAPTURE/SHIFT | 74157 S |
| GPIO10 MOSI | 스캔 데이터 입력 | 셀1 |
| GPIO9 MISO | 스캔 데이터 출력 | 셀7 Q (5V 보드면 3.3V 로 레벨다운 필수) |
| GPIO11 SCLK | 스캔 클럭 | 74273(shift) CP |

스캔체인 순서 (셀1 = Jetson MOSI 쪽):

| 셀 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| 신호 | CK | MOSI | RST | SCK | SS | DONE | MISO |
| 칩 핀 | 28 | 27 | 26 | 25 | 24 | 23 | 22 |
| 역할 | 바이패스 | 구동 | 바이패스 | 구동 | 구동 | 캡처 | 캡처 |
| TX 비트 | 0 | **1** | 2 | **3** | **4** | 5 | 6 |
| RX 비트 | 1 | 2 | 3 | 4 | 5 | **6** | **7** |

---

## 3. Jetson Nano 준비 (처음 한 번)

```bash
sudo /opt/nvidia/jetson-io/jetson-io.py      # SPI1(헤더 19/21/23/24) 활성화 → 저장 후 재부팅
```

```bash
sudo modprobe spidev && ls /dev/spidev*       # /dev/spidev0.0 이 보여야 함
```

```bash
sudo pip3 install spidev Jetson.GPIO
```

저장소를 Jetson 에 클론합니다 (공개 저장소라 로그인 없이 클론 가능).

```bash
git clone https://github.com/jhlee001010/scancell_testbench.git ~/scancell_testbench
```

```bash
cd ~/scancell_testbench/7cell
```

Jetson 이 인터넷에 연결되어 있지 않으면 PC 에서 폴더째 복사해도 됩니다.

```bash
scp -r scancell_testbench nano@<젯슨IP>:~/
```

> Jetson 에서는 반드시 **`python3`** 로 실행합니다. `python` 은 Python 2 라서 한글 주석에서 SyntaxError 가 납니다.

---

## 4. 실행 순서

### 4-1. 보드 자가진단 (소켓 비운 상태)

```bash
sudo python3 scan7_selftest.py
```

| 단계 | 내용 | 통과 기준 |
|---|---|---|
| [1] 체인 길이 | 0 으로 채운 뒤 1 을 밀어 처음 1 이 나오는 위치 | **7** |
| [2] 비트 배치 | 패턴 p 를 넣고 다음 전송에서 `(p << 1)` 이 나오는지 | 20개 모두 일치 |
| [3] 구동→캡처 | SS/SCK/MOSI walking-1/0 → UPDATE → CAPTURE → 되읽기 | 모두 일치 |
| [4] 직결 신호 | GPIO17/27 토글 → CK/RST 셀로 캡처 | 참고용 |

### 4-2. 칩 꽂고 자가진단

```bash
sudo python3 scan7_selftest.py --chip
```

[5] 리셋 중 `DONE=0, MISO=0` 이 나오면 칩 전원과 출력 경로가 정상입니다.

### 4-3. 벡터 몇 개만 먼저

```bash
sudo python3 scan7_vec_test.py --bins 0,1
```

### 4-4. 전체 실행

```bash
sudo python3 scan7_vec_test.py --chip chip01 --yield yield.csv --progress 20000
```

---

## 5. scan7_vec_test.py 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--bins 0,1` / `--bins 3-5` | 전부 | 실행할 bin. 모든 bin 이 리셋으로 시작해서 따로 실행 가능 |
| `--chip 이름` | `chip01` | 로그 파일 이름과 yield.csv 첫 칸 |
| `--log 경로` | `measured_<칩>.txt` | 실측 로그. `none` 이면 안 씀 |
| `--yield 경로` | 없음 | 결과 한 줄을 CSV 에 추가 (칩 여러 개 수율 집계) |
| `--speed Hz` | 500000 | 스캔 SPI 속도 |
| `--stop-on-fail` | 꺼짐 | 첫 FAIL bin 에서 멈춤 |
| `--max-report N` | 5 | bin 당 출력할 불일치 상세 개수 |
| `--progress N` | 0 | N 벡터마다 진행 표시 |
| `--no-follow` | 꺼짐 | 출력 셀에 기대 출력을 미러하지 않음 |
| `--vec`, `--bins-csv` | 자동 탐색 | 입력 파일 경로 직접 지정 |

### 화면 출력 예

```
bin  0 reset_idle                     44 vec     0.0s  PASS
bin  1 spi_loopback_zero             156 vec     0.1s  FAIL first@60 miso_err=1 done_err=0
    idx     60  in(rstn ssn sclk mosi)=1001  기대 miso=0 done=0  측정 miso=1 done=0
```

- `FAIL first@60` : bin 안에서 처음 틀린 벡터 번호
- `in(...)=1001` : 그 벡터에서 칩에 넣은 입력 (rstn ssn sclk mosi)
- `핀되읽기불일치=SCK(25번)` : 칩 판정과 별개로, 우리가 구동한 핀 값이 되읽기에서 달랐음 → **보드/접촉 문제**

### 실측 로그로 WSL 에서 다시 판정

```bash
python3 vec/compare_log.py out/expect_gls.txt measured_chip01.txt vec/stim_bins.csv chip01
```

```bash
python3 vec/decode_results.py vec/stim.txt measured_chip01.txt vec/stim_marks.txt vec/stim_bins.csv
```

---

## 6. 벡터 1개를 재생하는 순서

| 순서 | 동작 | 코드 | 원본 `sim/tb_vec.v` |
|:---:|---|---|---|
| ① | 새 입력 SHIFT + 직전 결과 꺼내기 | `hw.shift(tx)` | |
| ② | RST 직접 설정 | `hw.set_rst(rstn)` | i_clk 하강 직후 입력 인가 |
| ③ | 셀 → 칩 핀 반영 | `hw.update()` | i_clk 하강 직후 입력 인가 |
| ④ | 칩 클럭 1번 | `hw.clock()` | i_clk 상승 |
| ⑤ | 칩 출력 저장 | `hw.capture()` | 다음 하강 직전 샘플 |

결과는 **한 벡터 늦게** (다음 벡터의 ① 에서) 나옵니다. bin 마지막에는 더미 SHIFT 를 한 번 더 해서 꺼냅니다.

---

## 7. 문제가 생기면 고칠 곳 (`scan7_hw.py` 1장)

| 증상 | 원인 후보 | 고칠 설정 |
|---|---|---|
| selftest [1] 체인 길이 None | CAPTURE 극성 반대, MISO 선 | `CAP_LEVEL_CAPTURE` 0↔1 |
| selftest [1] 길이 0 | MISO 가 High 고정 | 배선 / 레벨시프터 방향 |
| selftest [2] 전부 반전되어 나옴 | 스캔 경로에 74HC14 1단 | `SCAN_TX_INVERT` / `SCAN_RX_INVERT` |
| selftest [3] 전부 FAIL | UPDATE 극성 / 신호 | `INVERT["UPD"]`, GPIO22 배선 |
| selftest [3] 일부 셀 FAIL | 그 셀 배선 | 회로 |
| 칩 bin 0 부터 FAIL | CK/RST 반전, 칩 전원 | `INVERT["CLK"]`, `INVERT["RST"]` |
| 체인 순서가 다름 | 셀 배선 순서 | `CHAIN` 리스트 순서 |
| 불안정 (가끔 FAIL) | 배선 길이, 속도 | `--speed 250000`, `PULSE_S`, `SETTLE_S` |

> 주의: **구동 셀 두 개(예: SS 와 SCK)가 서로 바뀌어 배선된 경우는 자가진단으로 잡히지 않습니다.**
> 되읽기도 똑같이 바뀌어 나오기 때문입니다. 이 경우는 칩을 꽂고 bin 0~1 을 돌리면 FAIL 로 드러납니다.

---

## 8. PC 에서 하드웨어 없이 돌려 보기 (dryrun)

Jetson·보드·칩 없이 Windows/WSL 에서 스크립트 흐름과 비트 배치를 확인합니다.
가상 칩은 실제 연산을 하지 않고 **정답(vectors.bin 의 기대값)을 그대로 내는 칩** 입니다.
대신 클럭 순간 칩 입력이 vectors.bin 과 같은지 검사해서 TX 배치 오류를 잡습니다.

```bash
python dryrun/run_dryrun.py selftest
```

```bash
python dryrun/run_dryrun.py vec --bins 0,1
```

```bash
python dryrun/run_dryrun.py vec
```

고장 주입 (bash):

```bash
FAKE_FAULT_MISO=60 python dryrun/run_dryrun.py vec --bins 0,1
```

```bash
FAKE_FAULT_PIN=SCK python dryrun/run_dryrun.py selftest
```

### dryrun 확인 결과 (2026-10-25)

| 시험 | 결과 |
|---|---|
| selftest (소켓 비움) | [1] 길이 7, [2] 20/20, [3] OK, [4] OK → **PASS** |
| selftest `--chip` | [5] DONE=0 MISO=0 → **PASS** |
| 전체 183,877 벡터 | 17 bin 모두 **PASS**, 가상 칩 입력 불일치 0개 |
| 실측 로그 → WSL `compare_log.py` | 17 bin 모두 **PASS** (로그 형식 호환 확인) |
| `FAKE_FAULT_MISO=60` | bin 1 **FAIL first@60** 으로 정확히 검출 |
| `FAKE_FAULT_PIN=SCK` | selftest [3] SCK FAIL, 벡터 재생에서 핀 되읽기 불일치 표시 |
| CAPTURE 극성 반대 | selftest [1] 체인 길이 None 으로 검출 |
| 보드 체인에서 SS↔SCK 바뀜 | 가상 칩 입력 불일치 84개로 검출 (자가진단으로는 안 잡힘 → 7장 주의) |

---

## 9. 아직 실물로 확인 안 된 가정

| 가정 | 확인 방법 |
|---|---|
| G23 HIGH = CAPTURE | selftest [1] |
| 스캔 경로·제어선에 74HC14 반전 없음 (또는 2단) | selftest [2], [3] |
| 셀1 = CK 가 Jetson MOSI 쪽 | selftest [2] + 칩 bin 0~1 |
| G27 이 74273 MR̅(스캔셀 클리어)과 분리되어 칩 RST 에만 연결 | 회로 확인 — 같이 묶여 있으면 리셋 해제 순간 SS 가 0→1 로 올라가 명령이 잘못 실행될 수 있음 |
| DONE/MISO 셀이 칩 핀을 구동하지 않음 (또는 저항 경유) | 회로 확인 — 저항 없이 직접 구동하면 칩 출력과 충돌 |
| Python 3.6 (JetPack 4) 에서 동작 | Jetson 에서 첫 실행 (3.6 에 없는 문법은 쓰지 않음) |
