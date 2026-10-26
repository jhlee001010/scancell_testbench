# 7cell 회로 점검 — `7pin_claude.circ` 핀 단위 추적 결과

> 점검일 2026-10-25 · 대상: Google Drive `MyChips/scancell(7pin)/7pin_claude.circ` (Logisim-evolution 4.1.0)
> 방법: 회로 파일의 와이어를 전부 넷으로 묶고, 74HC 칩 핀 번호(데이터시트 기준)에 대응시켜 연결을 하나씩 확인
> DUT(74161)는 자리만 채운 가짜 칩이라, **DUT 윗줄 핀 위치 = 실제 칩 윗줄 순서(CK·MOSI·RST·SCK·SS·DONE·MISO)** 로 보고 해석

> ### 2026-10-26 업데이트 — 실제 보드 확인 결과
> `7pin_claude.circ` 는 실제 브레드보드와 일부 달랐음. 실제 보드 기준:
>
> | 항목 | 실제 보드 | 코드 |
> |---|---|---|
> | 74HC14 반전 (CLK/RST/UPD/CAP/MOSI) | 모두 1번 | `INVERT` 전부 True, `SCAN_TX_INVERT=True` (그대로) |
> | Jetson SCLK → shift 74273 CP | **연결되어 있음** (회로 파일만 누락) | — |
> | 캡처 입력 | 74157 **B** | `CAP_LEVEL_CAPTURE=1` (그대로) |
> | 74273 ×2 /MR (nCLR) | **칩 RST 에서 분리, 5V 고정으로 수정함** | `RST_CLEARS_SCAN=False`, 시작 시 `safe_state()` 로 FF 초기값 덮어쓰기 |
>
> 아래 1~5장은 회로 파일 기준 원래 점검 내용이며, 1장의 1·2·6번은 위 표로 해결됨. DONE 셀·전원·/E 는 실제 보드에서 확인 필요.

---

## 1. 결론 먼저

| # | 항목 | 회로 상태 | 영향 | 조치 |
|:---:|---|---|---|---|
| 1 | **Jetson SCLK** | 어디에도 연결 안 됨 | 체인이 시프트되지 않음 | 🔧 **회로 수정 필수**: SCLK → shift 74273 CP |
| 2 | **shift 74273 CP** | UPDATE(G22) 넷에 연결 (update 74273 CP 와 같은 넷) | 시프트할 때마다 칩 핀도 같이 바뀜 | 🔧 위 1번과 함께 분리 |
| 3 | **DONE 셀** | 없음 (74157 2B 에만 연결, 2A/2Y 미연결, FF 비트 5 미사용) | DONE 을 읽을 수 없음, 체인 길이 6 | 🔧 **회로 수정 필수** (3장) |
| 4 | **74HC14 반전** | CLK·RST·UPDATE·CAPTURE·스캔 MOSI 모두 1단 (반전) | 그대로 쓰면 전부 반대 | ✅ 코드 반영 (`INVERT`, `SCAN_TX_INVERT`) |
| 5 | **CAPTURE 극성** | 74157 A=앞 셀(시프트), B=칩 핀(캡처) → S=1 이 캡처 | — | ✅ 코드 반영 (`CAP_LEVEL_CAPTURE=1`) |
| 6 | **RST ↔ 74273 /MR** | 칩 RST 와 74273 ×2 의 /MR 이 같은 넷 | 리셋 중 셀 전부 0 → SS=0, 리셋 해제 때 SS 0→1 | ✅ 코드에서 순서로 우회 (`RST_CLEARS_SCAN`) · 🔧 분리 권장 |
| 7 | **구동 셀 캡처 입력** | MOSI/SCK/SS 셀의 74157 B 미연결 | 구동 값 되읽기(배선 검사) 불가 | ✅ 코드에서 건너뜀 (`CAPTURE_WIRED`) · 🔧 연결 권장 |
| 8 | **전원** | 74245(입력쪽) GND 핀이 VCC 레일에, 74273(update) VCC/GND 미연결 | 실제 보드면 동작 안 함 / 단락 | 🔧 회로 수정 |
| 9 | **74157 /E** | 두 칩 모두 미연결 | 실제 보드에서 떠 있으면 출력 불안정 | 🔧 GND 로 |
| 10 | **부팅 중 상태** | GPIO 풀다운 + 반전 1단 → 칩 RST=High, UPDATE·CLK=High | 부팅/종료 후 칩이 리셋 해제 상태 | 🔧 GPIO 쪽 풀업으로 바꾸거나 반전 2단 |

✅ = 이번에 코드(`7cell/scan7_hw.py` 등)에 반영함 🔧 = 회로/배선을 고쳐야 함

---

## 2. 신호별 실제 경로

### 2-1. Jetson → 보드 (제어선, 스캔 입력)

| Jetson | 경로 | 도착 | 반전 |
|---|---|---|:---:|
| G17 DUT_CLK | 74HC14 6A→6Y → 74245 A1→B1 | 칩 CK | 1 |
| G27 RESET | 74HC14 5A→5Y → 74245 A2→B2 | 칩 RST **+ 74273 ×2 /MR** | 1 |
| G22 UPDATE | 74HC14 4A→4Y → 74245 A3→B3 | 74273 ×2 CP (shift + update 둘 다) | 1 |
| G23 CAPTURE | 74HC14 1A→1Y → 74245 A4→B4 | 74157 ×2 S | 1 |
| MOSI | 74HC14(다른 칩) 6A→6Y → 74245 A5→B5 | 셀1 (74157#1 1A) | 1 |
| **SCLK** | **연결 없음** | — | — |

- 74245(입력쪽): DIR=VCC (A→B), /OE=GND
- 74HC14 는 두 개 중 하나는 게이트 1개(MOSI)만, 다른 하나는 4개(CLK/RST/UPD/CAP) 사용

### 2-2. 스캔체인 (shift 74273 = 아래 표의 FF)

| 셀 | mux (74157) | shift FF | 다음 셀로 | update FF → 칩 핀 | 캡처 입력 (B) |
|:---:|---|---|---|---|---|
| 1 (CK, 더미) | #1 채널1: A=MOSI | D0/Q0 | Q0 → #1 2A | — | 비어 있음 |
| 2 (MOSI) | #1 채널2: A=Q0 | D1/Q1 | Q1 → #1 3A | U-D1/Q1 → **칩 MOSI** | 비어 있음 |
| 3 (RST, 더미) | #1 채널3: A=Q1 | D2/Q2 | Q2 → #1 4A | — | 비어 있음 |
| 4 (SCK) | #1 채널4: A=Q2 | D3/Q3 | Q3 → #2 1A | U-D3/Q3 → **칩 SCK** | 비어 있음 |
| 5 (SS) | #2 채널1: A=Q3 | D4/Q4 | **Q4 → #2 3A** (DONE 셀 건너뜀) | U-D4/Q4 → **칩 SS** | 비어 있음 |
| ~~6 (DONE)~~ | #2 채널2: **B=칩 DONE 만**, A·Y 없음 | D5/Q5 **미사용** | — | — | 칩 DONE (그러나 Y 가 어디에도 안 감) |
| 6 (MISO) | #2 채널3: A=Q4, **B=칩 MISO** | D6/Q6 | Q6 → 74245(출력쪽) A1→B1 → **Jetson MISO** | — | 칩 MISO ✅ |

- 비트 순서(셀1 = Jetson MOSI 쪽 = CK 더미)와 칩 핀 대응(셀2=MOSI, 4=SCK, 5=SS, 마지막=MISO)은 **의도와 일치**
- 차이는 **DONE 셀이 빠져서 체인이 6셀** 이라는 점
- 74245(출력쪽): DIR=VCC (A→B), /OE=GND, 별도 전원(Constant/Pin) — 3.3V 로 내리는 용도라면 **74LVC245 + 3.3V 전원** 이어야 함

---

## 3. 회로 수정안 (의도한 7셀로)

### 3-1. 반드시

1. **SCLK 분리**
   - shift 74273(#1) **CP(11번)** 를 UPDATE 넷에서 떼고 **Jetson SCLK** 에 연결
   - update 74273(#2) CP 는 UPDATE 그대로
   - SCLK 도 74HC14 를 거친다면 반전 횟수에 주의 (SPI mode 0 기준 상승 에지에서 시프트해야 함 → 반전 1단이면 2단으로)
2. **DONE 셀 추가** (74157#2 채널2 + 74273#1 비트5)
   - 74157#2 **2A(5번) ← shift Q4(12번)**
   - 74157#2 **2Y(7번) → shift D5(14번)**
   - 74157#2 **3A(11번) ← shift Q5(15번)** (지금 Q4 에 붙어 있는 선을 Q5 로 옮김)
   - 2B(6번) ← 칩 DONE 은 이미 연결됨
   - DONE 은 칩 출력이므로 update FF 는 필요 없음
3. **전원**
   - 74245(입력쪽) **GND(10번) → GND** (지금 VCC 레일)
   - 74273(update) **VCC(20번) → VCC, GND(10번) → GND**
4. **74157 ×2 의 /E(15번) → GND**

### 3-2. 권장

5. **74273 /MR(1번) 을 칩 RST 에서 분리** → VCC 고정 (또는 별도 GPIO)
   - 지금은 리셋 중 SS 가 0 이 되고, 리셋을 푸는 순간 SS 가 0→1 로 올라간다.
     칩 SPI 가 SS 상승 에지를 비동기로 보면 빈 명령(LOAD_W addr0 data0)이 실행될 수 있음.
   - 분리하면 `scan7_hw.py` 의 `RST_CLEARS_SCAN = False`
6. **구동 셀(MOSI/SCK/SS) 74157 B ← 해당 칩 핀** 연결
   - 칩 핀에 실제로 걸린 값을 되읽어 단선·접촉 불량을 잡을 수 있음
   - 연결하면 `CAPTURE_WIRED = ("MOSI", "SCK", "SS", "DONE", "MISO")`
7. **부팅 중 안전 상태**: G27 쪽을 풀업으로 바꾸거나(반전 1단 → 칩 RST=Low), 반전 2단으로
8. **입력 레벨**: Jetson 3.3V → 74HC14(5V) 는 문턱(최대 ~3.15V)에 걸쳐 불안정할 수 있음
   → **74HCT14** 를 쓰거나, 순서를 Jetson → 74HCT245 → 74HC14 로

---

## 4. 코드에 반영한 설정 (`7cell/scan7_hw.py`)

```python
CAP_LEVEL_CAPTURE = 1            # 74157 S=1 → B(칩 핀) = 캡처
INVERT = {"CLK": True, "RST": True, "UPD": True, "CAP": True}   # 74HC14 1단
SCAN_TX_INVERT = True            # MOSI 도 74HC14 1단
SCAN_RX_INVERT = False           # MISO 는 74245 만
RST_CLEARS_SCAN = True           # RST 넷 = 74273 /MR
CAPTURE_WIRED = ("DONE", "MISO") # DONE 은 3-1 의 2번 수정 후 동작
```

| 회로를 고치면 | 바꿀 설정 |
|---|---|
| /MR 분리 (3-2 의 5) | `RST_CLEARS_SCAN = False` |
| 구동 셀 B 연결 (3-2 의 6) | `CAPTURE_WIRED` 에 `"MOSI", "SCK", "SS"` 추가 |
| 반전을 2단으로 | 해당 `INVERT` 값을 `False` |
| DONE 셀 없이 6셀로 쓰기로 함 | `CHAIN` 에서 `"DONE"` 제거 + `vectors.bin` 의 DONE 마스크 무시 처리 필요 (별도 작업) |

### RST 가 셀을 지우는 회로에서의 재생 순서 (`scan7_vec_test.py`)

| 벡터 | 순서 | 이유 |
|---|---|---|
| rstn = 1 | **RST 올림 → SHIFT** → UPDATE → CK → CAPTURE | RST=0 인 동안 SHIFT 하면 셀이 바로 지워짐 |
| rstn = 0 | **SHIFT → RST 내림** → UPDATE → CK → CAPTURE | 먼저 내리면 직전 벡터 결과가 지워짐 |

dryrun 에서 이 순서를 쓰지 않으면 리셋 직후 벡터에서 칩 SS 가 1 대신 0 으로 들어가는 것을 확인함 (bin 0~2 에서 3회).
리셋 벡터 136개와 그 직전 벡터의 기대 출력은 모두 0 이라, 리셋 중 셀이 지워져도 판정에는 영향 없음.

---

## 5. dryrun 확인 (위 설정 + 회로 특성을 가상 보드에 그대로 모델링)

| 시험 | 결과 |
|---|---|
| selftest | 체인 7, 비트 배치 20/20, [3][4] 는 캡처 입력 미연결로 건너뜀 → PASS |
| selftest `--chip` | 리셋 직후 DONE=0 MISO=0 → PASS |
| 전체 183,877 벡터 | 17 bin PASS, 가상 칩 입력 불일치 0 |
| 순서 우회 끔 (`RST_CLEARS_SCAN=False` 인데 회로는 /MR 연결) | 입력 불일치 3회 (리셋 직후 SS=0) → 우회가 필요함을 확인 |
| 지금 회로 그대로 (DONE 셀 없음, 6셀) | selftest [1] 체인 길이 **6 / 7 FAIL** 로 바로 드러남 |
