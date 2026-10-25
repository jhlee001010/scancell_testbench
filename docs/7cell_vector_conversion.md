# 7핀 스캔셀로 Convolution Accelerator v1 테스트하기 — 테스트 벡터 변환 정리

> 작성일 2026-10-23 (scancell_testbench 저장소 `docs/` 로 옮김: 2026-10-25)
> 2장의 Windows 경로는 Google Drive `MyChips` 폴더 기준입니다. 저장소 안에서는 벡터가 `vectors/conv_acc_v1/`, 핀맵 PDF 가 `docs/conv_acc_pinmap_v1.pdf` 에 있습니다. · 대상 칩: **MyChip_FP_MAC (Convolution Accelerator v1, SOP28)**
> 목적: WSL `~/test_vector` 에서 만든 테스트 벡터(`vectors.bin`)를 **7핀 스캔셀 보드 + Jetson Nano(GPIO/SPI)** 로 재생할 수 있게 변환하는 방법을 한 문서에 정리

---

## 목차

1. [한눈에 보기](#1-한눈에-보기)
2. [관련 파일 위치](#2-관련-파일-위치)
3. [칩 핀맵 (SOP28)](#3-칩-핀맵-sop28)
4. [Jetson Nano GPIO 할당](#4-jetson-nano-gpio-할당)
5. [스캔체인 구성과 비트 배치](#5-스캔체인-구성과-비트-배치)
6. [원본 테스트 벡터 형식 (WSL test_vector)](#6-원본-테스트-벡터-형식-wsl-test_vector)
7. [변환 규칙: vectors.bin 1바이트 → 스캔 SPI 1바이트](#7-변환-규칙-vectorsbin-1바이트--스캔-spi-1바이트)
8. [벡터 1개 재생 순서 (타이밍)](#8-벡터-1개-재생-순서-타이밍)
9. [출력 형식 결정: 무엇을 만들 것인가](#9-출력-형식-결정-무엇을-만들-것인가)
10. [보드 확인 순서 (bring-up)](#10-보드-확인-순서-bring-up)
11. [주의사항 / 위험 요소](#11-주의사항--위험-요소)
12. [아직 확인이 필요한 것](#12-아직-확인이-필요한-것)
13. [부록 A. bin 목록](#부록-a-bin-목록)
14. [부록 B. 칩 명령어 요약](#부록-b-칩-명령어-요약)
15. [부록 C. 자주 쓰는 명령어](#부록-c-자주-쓰는-명령어)

---

## 1. 한눈에 보기

```
 WSL ~/test_vector                       Jetson Nano                        7핀 스캔셀 보드              칩
┌──────────────────────┐   복사    ┌───────────────────────┐  SPI0   ┌──────────────────────┐   ┌───────────┐
│ gen_vectors.py       │ ───────> │ vectors.bin           │ ──────> │ 셀1 CK   (바이패스)    │──>│ 28 CK     │
│  -> stim.txt         │          │ stim_bins.csv         │  MOSI   │ 셀2 MOSI              │──>│ 27 MOSI   │
│ iverilog GLS         │          │                       │  SCLK   │ 셀3 RST  (바이패스)    │──>│ 26 RST    │
│  -> expect_gls.txt   │          │ 재생 스크립트          │  CE0    │ 셀4 SCK               │──>│ 25 SCK    │
│ pack_for_fpga.py     │          │  (벡터마다 변환)       │ <────── │ 셀5 SS                │──>│ 24 SS     │
│  -> vectors.bin      │          │                       │  MISO   │ 셀6 DONE (캡처)        │<──│ 23 DONE   │
└──────────────────────┘          │ GPIO17 CK  ───────────┼─────────┼──── 직결 ─────────────┼──>│ 28 CK     │
                                  │ GPIO27 RST ───────────┼─────────┼──── 직결 ─────────────┼──>│ 26 RST    │
                                  │ GPIO22 UPDATE         │         │ 셀7 MISO (캡처)        │<──│ 22 MISO   │
                                  │ GPIO23 CAPTURE        │         └──────────────────────┘   └───────────┘
                                  └───────────────────────┘
```

**핵심 요약**

| 항목 | 내용 |
|---|---|
| 테스트 벡터 수 | **183,877개** (벡터 1개 = 칩 `i_clk` 1사이클) |
| 원본 파일 | `vectors.bin` — 벡터당 1바이트 (입력 4비트 + 기대값 2비트 + 마스크 2비트) |
| 스캔체인 길이 | **7셀** (CK → MOSI → RST → SCK → SS → DONE → MISO 순서) |
| 스캔 SPI 길이 | 벡터당 **1바이트(8비트)** 전송이면 충분 |
| 바이패스 신호 | **CK(28번), RST(26번)** — 스캔셀을 거치지 않고 GPIO 에서 직접 |
| 스캔셀로 구동 | SS, SCK, MOSI (칩 입력 3개) |
| 스캔셀로 캡처 | DONE, MISO (칩 출력 2개) |
| 변환 방식 (권장) | 별도 파일을 만들지 않고, **재생 스크립트가 `vectors.bin` 을 읽으면서 바로 변환** |

---

## 2. 관련 파일 위치

### Windows (Google Drive)

| 경로 | 내용 |
|---|---|
| `MyChips\scancell(7pin)\conv_acc_pinmap_v1.pdf` | 칩 SOP28 핀맵 |
| `MyChips\scancell(7pin)\7pin.circ` | 7핀 스캔셀 Logisim 회로 |
| `MyChips\scancell(7pin)\scancell_gpio_pinmap.xlsx` | 스캔셀 테스트용 GPIO 할당표 (Pi Wedge 기준) |
| `MyChips\scancell(7pin)\ALGORITHM_GUIDE.md` | 칩/테스트 벡터 전체 가이드 |
| `MyChips\testvector\vectors.bin` | 테스트 벡터 (WSL 원본과 **완전히 같음**, md5 일치 확인) |
| `MyChips\testvector\stim_bins.csv` | bin 목차 (WSL 원본과 내용 같음, 줄바꿈만 CRLF) |
| `MyChips\testvector\test_circuit.circ` | 테스트 회로 |

### WSL (`Ubuntu`, 경로 `/home/mplab/test_vector`)

> `ALGORITHM_GUIDE.md` 안의 링크는 `/home/jeheexy/test_vector` 로 되어 있지만, 현재 PC 의 실제 경로는 **`/home/mplab/test_vector`** 입니다.

| 경로 | 내용 |
|---|---|
| `vec/gen_vectors.py` | 입력 벡터 생성기 → `stim.txt`, `stim_bins.csv`, `stim_marks.txt` |
| `vec/stim.txt` | 입력 대본. 한 줄 = `rstn ssn sclk mosi` 4글자 |
| `sim/tb_vec.v` | 벡터 재생 테스트벤치 (RTL / 게이트 넷리스트 공용) |
| `out/expect_gls.txt` | **정답지(golden)**. 한 줄 = `<idx> <miso> <cmd_done>` |
| `out/expect_rtl.txt` | RTL 정답지 (GLS 와 완전히 일치) |
| `vec/pack_for_fpga.py` | stim + expect → `out/vectors.bin`, `out/vectors.hex` |
| `vec/compare_log.py` | 실측 로그 vs 정답 → bin 별 PASS/FAIL |
| `vec/decode_results.py` | 0/1 비트열을 FP8 값으로 해석 |
| `pico/vec_test.py` | Pico 2 W 로 직접 재생하던 기존 테스터 (스캔셀 없이) |
| `pico/extract_measured.py` | Pico 로그에서 실측값만 추출 |
| `run_all.sh` | 생성 → RTL 시뮬 → GLS 시뮬 → 비교 → 패킹 전체 실행 (약 15초) |

---

## 3. 칩 핀맵 (SOP28)

`conv_acc_pinmap_v1.pdf` 기준. **점(●) 이 있는 왼쪽 아래가 1번 핀**입니다.
아래 줄은 왼쪽→오른쪽으로 1~14번, 위 줄은 오른쪽→왼쪽으로 15~28번입니다.

```
        28    27    26    25    24    23    22    21    20    19    18    17    16    15
        CK   MOSI  RST   SCK   SS   DONE  MISO  VSS   VSS   VSS   VSS   VSS   VSS   VSS
       ┌───────────────────────────────────────────────────────────────────────────────┐
       │ ●                                                                             │
       └───────────────────────────────────────────────────────────────────────────────┘
        VDD   VDD   VDD   VDD   VDD   VDD   VDD   VSS   VDD   VSS   VDD   VSS   VDD   VSS
         1     2     3     4     5     6     7     8     9    10    11    12    13    14
```

### 신호 핀

| 칩 핀 | 이름 | 넷리스트 포트 | 방향 | 극성 | 설명 |
|:---:|---|---|:---:|:---:|---|
| 28 | CK | `i_clk` | 입력 | — | 시스템 클럭. 벡터 1개 = 1펄스 |
| 27 | MOSI | `i_mosi` | 입력 | — | SPI 데이터 입력 (칩 기준) |
| 26 | RST | `i_rstn` | 입력 | **Active-Low** | 비동기 리셋. 0 이면 리셋 |
| 25 | SCK | `i_sclk` | 입력 | — | SPI 클럭 (Mode 0) |
| 24 | SS | `i_ssn` | 입력 | **Active-Low** | Slave Select. 0→1 에서 명령 확정 |
| 23 | DONE | `o_cmd_done` | 출력 | — | 명령 처리 완료 표시 |
| 22 | MISO | `o_miso` | 출력 | — | SPI 데이터 출력 (칩 기준) |

### 전원 핀

| 종류 | 핀 번호 |
|---|---|
| VDD | 1, 2, 3, 4, 5, 6, 7, 9, 11, 13 |
| VSS | 8, 10, 12, 14, 15, 16, 17, 18, 19, 20, 21 |

> 주의: 여기서 **MOSI/MISO/SCK/SS 는 "칩의" SPI 핀**입니다. 스캔셀 보드를 움직이는 Jetson 의 SPI(아래 4장)와 이름이 같으니 헷갈리지 않게 문서에서는 칩 쪽을 `i_mosi`, `o_miso` 처럼 넷리스트 이름으로도 함께 적습니다.

---

## 4. Jetson Nano GPIO 할당

`scancell_gpio_pinmap.xlsx` 기준. 번호는 **BCM 번호 = SparkFun Pi Wedge 라벨** 입니다.

### 4-1. 제어용 GPIO

| 용도 | Pi Wedge 라벨 | BCM | 40핀 헤더 | 부팅 기본 | 메모 |
|---|---|:---:|:---:|---|---|
| DUT CLK (칩 `i_clk` 직결) | G17 | 17 | 11 | 풀다운 (LOW) | 소프트웨어 토글. 연속 하드웨어 클럭이 필요하면 GPIO4/18 로 이동 |
| 전체 RESET | G27 | 27 | 13 | 풀다운 (LOW) | Active-Low. 부팅 중 리셋 유지 → 안전. 코드 시작 시 HIGH 로 해제 |
| 스캔셀 UPDATE | G22 | 22 | 15 | 풀다운 (LOW) | 부팅 중 LOW 유지 → 의도치 않은 래치 방지 |
| CAPTURE / SHIFT 선택 | G23 | 23 | 16 | 풀다운 (LOW) | 스캔셀 mux 선택 |

### 4-2. 스캔체인 SPI (SPI0)

| 신호 | Pi Wedge 라벨 | BCM | 40핀 헤더 | 방향 | 메모 |
|---|---|:---:|:---:|---|---|
| MOSI | MOSI | 10 | 19 | Jetson → 스캔셀 | 3.3V 출력. HCT 입력(VIH 2.0V)이면 충분 |
| MISO | MISO | 9 | 21 | 스캔셀 → Jetson | **5V 보드면 반드시 3.3V 로 레벨시프트/분압** |
| SCLK | SCLK | 11 | 23 | Jetson → 스캔셀 | |
| CS | CE0 | 8 | 24 | Jetson → 스캔셀 | CE1(GPIO7, 26번)도 가능 |

### 4-3. 피하는 핀

| BCM | 기능 | 피하는 이유 |
|---|---|---|
| 0, 1 | ID_SD / ID_SC | HAT ID EEPROM 전용 |
| 2, 3 | I2C1 | 보드 풀업 저항 있음 |
| 4~8 | GPCLK0 / SPI0 CE | 부팅 기본 풀업 → 부팅 중 HIGH 튈 수 있음 |
| 9~11 | SPI0 | 스캔체인 SPI 로 사용 중 |
| 14, 15 | UART | 시리얼 콘솔 충돌 |
| 16~21 | SPI1 | 나중에 SPI1 쓰면 충돌 |

---

## 5. 스캔체인 구성과 비트 배치

### 5-1. 체인 순서 (확정된 내용)

- 체인은 **CK → MOSI → RST → SCK → SS → DONE → MISO** 순서 (칩 위쪽 줄 28번 → 22번 순서와 같음)
- **셀 1 = Jetson MOSI 바로 다음 셀**, **셀 7 = Jetson MISO 로 나가는 마지막 셀**
- **CK, RST 는 바이패스**: 체인에 셀은 있지만 칩에는 Jetson GPIO17/27 이 직접 들어감 → 이 두 셀 값은 **don't-care**

| 셀 | 칩 핀 | 신호 | 셀 역할 | 셀에 넣는 값 |
|:---:|:---:|---|---|---|
| 1 | 28 | CK | 바이패스 | 0 (의미 없음) |
| 2 | 27 | MOSI (`i_mosi`) | **구동** | 벡터의 mosi |
| 3 | 26 | RST (`i_rstn`) | 바이패스 | 0 (의미 없음) |
| 4 | 25 | SCK (`i_sclk`) | **구동** | 벡터의 sclk |
| 5 | 24 | SS (`i_ssn`) | **구동** | 벡터의 ssn |
| 6 | 23 | DONE (`o_cmd_done`) | **캡처** | 0 (칩 출력이므로 구동 안 함 — 12장 확인 필요) |
| 7 | 22 | MISO (`o_miso`) | **캡처** | 0 (위와 같음) |

### 5-2. SPI 1바이트와 셀의 대응

Jetson `spidev` 는 **MSB 먼저** 보냅니다. 체인이 7셀인데 8비트를 보내면, 맨 처음 보낸 비트(bit7)는 체인 끝까지 밀려 나가 버리고 나머지 7비트가 셀에 남습니다.

```
보내는 바이트 (TX)          bit7   bit6   bit5   bit4   bit3   bit2   bit1   bit0
                            버림   셀7    셀6    셀5    셀4    셀3    셀2    셀1
                                   MISO   DONE   SS     SCK    RST    MOSI   CK
```

동시에 받는 바이트(RX)는 **전송 직전에 셀에 들어 있던 값(= 직전 CAPTURE 결과)** 입니다.

```
받는 바이트 (RX)            bit7   bit6   bit5   bit4   bit3   bit2   bit1   bit0
                            셀7    셀6    셀5    셀4    셀3    셀2    셀1    TX bit7
                            MISO   DONE   SS     SCK    RST    MOSI   CK     (되돌아온 값)
```

> 정리: **TX 는 `셀 n → bit (n-1)`**, **RX 는 `셀 n → bit n`** 으로 한 칸씩 어긋납니다.
> 이 어긋남은 "체인 7셀 + SPI 8비트" 때문이고, 10장의 체인 길이 테스트로 반드시 실측 확인합니다.

| 신호 | TX 비트 (넣을 때) | RX 비트 (읽을 때) | 마스크 |
|---|:---:|:---:|:---:|
| CK (셀1) | bit0 | bit1 | 0x01 / 0x02 |
| MOSI (셀2) | **bit1** | bit2 | 0x02 / 0x04 |
| RST (셀3) | bit2 | bit3 | 0x04 / 0x08 |
| SCK (셀4) | **bit3** | bit4 | 0x08 / 0x10 |
| SS (셀5) | **bit4** | bit5 | 0x10 / 0x20 |
| DONE (셀6) | bit5 | **bit6** | 0x20 / **0x40** |
| MISO (셀7) | bit6 | **bit7** | 0x40 / **0x80** |

RX 의 SS/SCK/MOSI 비트(bit5, bit4, bit2)는 **방금 구동한 입력이 칩 핀에 제대로 걸렸는지 되읽기(readback)** 로 쓸 수 있습니다. 단락·접촉 불량을 잡는 데 유용합니다.

---

## 6. 원본 테스트 벡터 형식 (WSL test_vector)

### 6-1. 만들어지는 흐름

```
gen_vectors.py ──> stim.txt (입력 4비트 × 183,877줄)
                      │
                      ├──> iverilog RTL 시뮬 ──> expect_rtl.txt ─┐
                      └──> iverilog GLS 시뮬 ──> expect_gls.txt ─┴─ diff → MATCH
                                                   │
pack_for_fpga.py (stim + expect_gls) ──────────> vectors.bin (1바이트/벡터)
```

- **golden(정답) = `expect_gls.txt`** — 실제 칩 넷리스트 `TOP_new.v` 를 돌린 결과
- RTL 과 GLS 결과는 **완전히 일치(MATCH)**, GLS 에 X(미정값) 사이클 **0개**

### 6-2. stim.txt / expect_gls.txt

```
stim.txt                 expect_gls.txt
0100   ← rstn=0 ssn=1    0 0 0     ← idx 0: miso=0 done=0
0100                     1 0 0
...                      ...
```

- `stim.txt` 한 줄 = `rstn ssn sclk mosi` 순서 4글자
- `expect_gls.txt` 한 줄 = `<벡터번호> <miso> <cmd_done>`, 정할 수 없으면 `x`

### 6-3. vectors.bin 바이트 형식

| 비트 | 7 | 6 | 5 | 4 | 3 | 2 | 1 | 0 |
|---|---|---|---|---|---|---|---|---|
| 의미 | MISO 마스크 | MISO 기대값 | DONE 마스크 | DONE 기대값 | rstn | ssn | sclk | mosi |

- 마스크 = 1 이면 비교, 0 이면 무시
- 예: `0xA4` = `1010 0100` → MISO 비교(기대 0), DONE 비교(기대 0), **rstn=0, ssn=1** (리셋 구간)
- 예: `0xAC` = `1010 1100` → rstn=1, ssn=1 (대기 상태)

### 6-4. 실제 파일 통계 (확인한 값)

| 항목 | 값 |
|---|---|
| 파일 크기 | 183,877 바이트 = 벡터 183,877개 |
| MISO 마스크=1 | 183,877개 (**전부 비교**, don't-care 없음) |
| DONE 마스크=1 | 183,877개 (**전부 비교**) |
| MISO 기대값=1 | 21,436개 |
| DONE 기대값=1 | 172,123개 |

입력 4비트 분포:

| 입력 (rstn ssn sclk mosi) | 개수 | 의미 |
|---|---:|---|
| `1000` | 67,058 | SS 활성, SCK low, MOSI 0 |
| `1010` | 56,974 | SS 활성, SCK high, MOSI 0 |
| `1001` | 23,842 | SS 활성, SCK low, MOSI 1 |
| `1011` | 23,826 | SS 활성, SCK high, MOSI 1 |
| `1100` | 12,041 | 대기 (SS 비활성) |
| `0100` | 136 | 리셋 |

### 6-5. 원본 타이밍 규칙 (`sim/tb_vec.v`)

1. `i_clk` **하강 직후** 입력 4개 인가
2. `i_clk` **상승** → 칩 내부 플립플롭 1사이클 진행
3. **다음 하강 직전** `o_miso`, `o_cmd_done` 샘플 → 그 벡터의 기대값

스캔셀에서는 이것을 "UPDATE(입력 인가) → CK 0→1→0 펄스 → CAPTURE(출력 저장)" 로 똑같이 흉내 냅니다.
칩은 전부 정적 논리라서 벡터 사이에 시간이 오래 걸려도 결과가 같습니다 (Pico 테스터에서도 확인된 사실).

---

## 7. 변환 규칙: vectors.bin 1바이트 → 스캔 SPI 1바이트

### 7-1. 한 줄 요약

```
vectors.bin 바이트 v
 ├─ rstn  = v >> 3 & 1   →  GPIO27 로 직접 출력
 ├─ ssn   = v >> 2 & 1   →  TX bit4 (셀5)
 ├─ sclk  = v >> 1 & 1   →  TX bit3 (셀4)
 ├─ mosi  = v      & 1   →  TX bit1 (셀2)
 ├─ MISO 기대값/마스크   →  RX bit7 과 비교
 └─ DONE 기대값/마스크   →  RX bit6 과 비교
 CK 는 GPIO17 로 벡터마다 펄스 1번
```

### 7-2. 변환 코드 (파이썬, 그대로 써도 되는 수준)

```python
# vectors.bin 1바이트 -> (스캔 TX 바이트, rstn GPIO 값, 기대 RX 값, RX 마스크)
def convert(v):
    rstn = (v >> 3) & 1
    ssn  = (v >> 2) & 1
    sclk = (v >> 1) & 1
    mosi =  v       & 1
    tx = (ssn << 4) | (sclk << 3) | (mosi << 1)       # 셀5=SS, 셀4=SCK, 셀2=MOSI

    mask = exp = 0
    if v & 0x80:                                       # MISO 비교
        mask |= 0x80; exp |= ((v >> 6) & 1) << 7       # RX bit7 = 셀7 = MISO
    if v & 0x20:                                       # DONE 비교
        mask |= 0x40; exp |= ((v >> 4) & 1) << 6       # RX bit6 = 셀6 = DONE
    return tx, rstn, exp, mask
```

### 7-3. 파이프라인 (중요)

스캔체인은 "넣으면서 동시에 빼는" 구조라서, **벡터 n 의 결과는 벡터 n+1 을 넣는 SPI 전송에서 나옵니다.**

```
SPI 전송 #0 : TX = 벡터0 입력      RX = (의미 없음, 리셋 직후 값)
SPI 전송 #1 : TX = 벡터1 입력      RX = 벡터0 결과   ← 벡터0 기대값과 비교
SPI 전송 #2 : TX = 벡터2 입력      RX = 벡터1 결과
   ...
SPI 전송 #N : TX = 0 (더미)        RX = 벡터N-1 결과 ← 마지막에 한 번 더 전송
```

→ 벡터 183,877개에 **SPI 전송 183,878번**. 전송 횟수가 벡터 수와 같아서 따로 "읽기 전용" 전송이 필요 없습니다.

---

## 8. 벡터 1개 재생 순서 (타이밍)

시작 조건: CK(GPIO17) = 0, UPDATE(GPIO22) = 0, CAPTURE(GPIO23) = SHIFT 쪽

| 순서 | 동작 | GPIO / SPI | 원본 테스트벤치와 대응 |
|:---:|---|---|---|
| ① | 스캔 SHIFT 모드 선택 | GPIO23 = SHIFT | |
| ② | 새 입력 밀어넣기 + 직전 결과 꺼내기 | SPI 1바이트 `xfer2([tx])` | (직전 벡터의 샘플 결과 회수) |
| ③ | RST 직접 인가 | GPIO27 = rstn | i_clk 하강 직후 입력 인가 |
| ④ | 셀 값을 칩 핀에 반영 | GPIO22 0→1→0 (UPDATE 펄스) | i_clk 하강 직후 입력 인가 |
| ⑤ | 칩 클럭 1번 | GPIO17 0→1→0 | i_clk 상승 |
| ⑥ | 칩 출력 저장 | GPIO23 = CAPTURE, 스캔 SCLK 1번(또는 SPI 1바이트), GPIO23 = SHIFT | 다음 하강 직전 샘플 |
| ⑦ | 다음 벡터로 | ①로 | |

**⑥ CAPTURE 를 SPI 로 할 때:** CAPTURE 모드에서 SPI 1바이트를 보내면 SCLK 가 8번 치지만, 모든 셀이 매번 같은 핀 값을 다시 저장할 뿐이라 결과는 같습니다 (ttl_scan 보드에서 쓰던 방식).

**예상 소요 시간 (Python, 대략):** 벡터당 SPI 2번 + GPIO 토글 약 8번 ≈ 0.3~0.5 ms → **전체 약 1~1.5분**. 칩 동작에는 영향 없음(정적 논리).

---

## 9. 출력 형식 결정: 무엇을 만들 것인가

| 방법 | 장점 | 단점 | 추천 |
|---|---|---|:---:|
| **A. 재생 스크립트가 vectors.bin 을 바로 읽어 변환** | 새 파일 불필요, 원본 하나만 관리, WSL 에서 벡터를 다시 만들어도 복사만 하면 끝 | 스크립트 안에 비트 배치가 들어감 | ★ |
| B. 변환 파일을 따로 생성 (`scan7_vectors.bin`: TX/기대/마스크 3바이트씩) | 재생 스크립트가 단순 | 파일 2종 관리, 벡터 재생성 시 변환 다시 | |
| C. 스캔셀용 텍스트 (`scan7_stim.txt`) | 사람이 읽기 쉬움 | 파일 큼, 느림 | |

**추천: A + 결과 로그는 `compare_log.py` 형식으로 저장**

```
Jetson:  sudo python3 scan7_vec_test.py vectors.bin stim_bins.csv
           ├─ 화면: bin 별 PASS / FAIL@벡터번호
           └─ measured_chip01.txt   ("<idx> <miso> <done>" 형식)

WSL:     python3 vec/compare_log.py out/expect_gls.txt measured_chip01.txt vec/stim_bins.csv chip01 >> yield.csv
         python3 vec/decode_results.py vec/stim.txt measured_chip01.txt vec/stim_marks.txt vec/stim_bins.csv
```

이렇게 하면 **기존 Pico 테스터와 똑같은 판정 도구(compare_log.py, decode_results.py)** 를 그대로 쓸 수 있고, 칩 여러 개를 테스트해서 `yield.csv` 에 한 줄씩 쌓을 수 있습니다.

---

## 10. 보드 확인 순서 (bring-up)

칩을 꽂기 **전에** 스캔셀 보드부터 확인합니다.

| 단계 | 칩 | 확인 내용 | 방법 | 통과 기준 |
|:---:|:---:|---|---|---|
| 1 | 없음 | 전원 / GND 공통 | 멀티미터 | Jetson GND = 보드 GND |
| 2 | 없음 | **체인 길이** | SHIFT 모드에서 0x00 여러 바이트 → 0xFF 여러 바이트 보내고 처음 1 이 나오는 위치 | **7** |
| 3 | 없음 | **비트 배치 확인** | 패턴(예: 0x2A) 보내고 다음 전송의 RX 확인 | 5-2절 표대로 한 칸 어긋나서 나옴 |
| 4 | 없음 | 구동 → 캡처 경로 | SS/SCK/MOSI 셀에 walking-1 → UPDATE → CAPTURE → 읽기 | 넣은 값 그대로 되읽힘 |
| 5 | 없음 | CK/RST 직결 | GPIO17/27 토글하며 소켓 28/26번 핀 전압 확인 | 3.3V/0V 정상 토글 |
| 6 | **있음** | 리셋 후 출력 | RST=0 → 1, CAPTURE | MISO=0, DONE=0 |
| 7 | 있음 | bin 0~1 만 | `RUN_BINS=[0,1]` | PASS |
| 8 | 있음 | 전체 183,877 벡터 | 전체 실행 | 17개 bin 모두 PASS |

> 2~3단계 결과가 표와 다르면(예: 체인 길이 8, 또는 비트가 반전되어 나옴) **5장의 비트 배치를 실측값에 맞춰 고친 뒤** 진행합니다. 74HC14 를 홀수 번 거치는 경로가 있으면 값이 반전됩니다.

---

## 11. 주의사항 / 위험 요소

### 11-1. RESET(G27) 이 스캔셀 FF 클리어와 칩 RST 를 같이 쓰는 경우 ⚠

엑셀에는 G27 이 "**전체 RESET**" 으로 되어 있습니다. 만약 G27 이 **74273 의 MR̅(클리어)** 과 **칩 RST(26번)** 에 동시에 연결돼 있다면:

- 리셋 중 스캔셀이 전부 0 → 칩 핀 **SS = 0** 이 됨 (원래 벡터는 리셋 중 SS = 1)
- 리셋 해제 후 첫 UPDATE 에서 SS 가 0 → 1 로 올라감 → 칩이 이것을 **"명령 확정(SS 상승 에지)"** 으로 받아들여, 비어 있는 프레임(전부 0 = `LOAD_W addr0 data 0`)을 실행할 수 있음
- 결과: bin 0 의 DONE 기대값과 어긋날 수 있음

**대책 (둘 중 하나)**
- 스캔셀 클리어와 칩 RST 를 **다른 GPIO 로 분리**
- 또는 소프트웨어에서 순서를 지킴: RST 해제 **전에** SS=1 을 SHIFT + UPDATE 해 둔다 (단, MR̅ 이 같이 묶여 있으면 리셋 중에는 불가능 → 분리가 정답)

### 11-2. 출력 핀(DONE, MISO) 셀의 구동 충돌

셀 6, 7 은 칩 **출력**을 캡처하는 셀입니다. 만약 이 셀들의 update FF 가 칩 핀을 **구동까지** 하는 구조라면, 칩 출력과 부딪힙니다.

- 구동하지 않는 구조(캡처 전용)면 문제없음
- 저항(예: 1kΩ)을 거쳐 구동하는 구조면, **셀 값을 직전 캡처값과 같게** 넣어 충돌 전류를 줄여야 함 (ttl_scan 보드와 같은 방식)
- 저항 없이 직접 구동하면 **배선 수정 필요**

### 11-3. 전압 레벨

- 칩 VDD 가 몇 V 인지에 따라 레벨시프터 구성이 바뀜
  - 칩이 5V 쪽이면: Jetson → 보드는 **74HCT245(5V 전원)** 로 3.3V → 5V, 보드 → Jetson MISO 는 **74LVC245(3.3V 전원) 또는 분압** 필수
  - 74HCT245 를 5V 로 쓰면서 DIR 을 반대로 해서 5V → 3.3V 로 쓰면 **Jetson 손상**
- 모든 GND 는 하나로 연결

### 11-4. 74HC14 반전

슈미트 트리거 74HC14 는 **반전 버퍼**입니다. 신호가 74HC14 를 1번(홀수 번) 거치면 반전되므로 소프트웨어에서 비트를 뒤집어야 합니다.

| 신호 경로 | 74HC14 통과 횟수 | 처리 |
|---|:---:|---|
| 2번 (짝수) | 2 | 그대로 |
| 1번 (홀수) | 1 | 소프트웨어에서 반전 |

→ CK(GPIO17), RST(GPIO27), UPDATE, CAPTURE, 스캔 MOSI/SCLK 각각 몇 번 거치는지 회로에서 확인 필요. Pico 테스터는 입력을 74HC14 1단으로 반전시켜 `INVERT_INPUTS = True` 로 보상했었음.

### 11-5. 클럭 / 리셋 글리치

`ALGORITHM_GUIDE.md` 의 권고대로 CK 와 RST 는 이미 **바이패스(직결)** 로 설계되어 있어, 스캔 SHIFT 중 글리치로 칩 상태가 깨질 위험은 없습니다. 다만 직결선에도 74HC14(슈미트) 를 거치게 하면 브레드보드 링잉에 의한 이중 클럭을 막을 수 있습니다.

### 11-6. 기타

- 각 IC 의 VCC–GND 사이 0.1µF
- 안 쓰는 74HC14 / 74157 입력은 GND 에 고정
- `W_I_RF` 는 래치로 합성돼 **리셋이 없음** → 모든 MAC 테스트는 W/I 18개를 먼저 채움 (벡터가 이미 그렇게 만들어져 있음)

---

## 12. 아직 확인이 필요한 것

| # | 항목 | 왜 필요한가 | 확인 방법 |
|:---:|---|---|---|
| 1 | 체인 끝의 정확한 위치 (셀1 = CK 가 Jetson MOSI 쪽인가) | 비트 배치가 통째로 뒤집힐 수 있음 | 10장 2~3단계 |
| 2 | G27 이 74273 MR̅ 과 칩 RST 를 같이 쓰는지 | 11-1 문제 | 회로/배선 확인 |
| 3 | DONE / MISO 셀이 칩 핀을 구동하는지 | 11-2 충돌 | 회로 확인 |
| 4 | 칩 동작 전압 (VDD) | 레벨시프터 구성 | 칩 사양 |
| 5 | 경로별 74HC14 통과 횟수 | 반전 처리 | 회로 확인 |
| 6 | CAPTURE(G23) 의 극성 (HIGH = 캡처인가 SHIFT 인가) | 모드 선택 | 74157 S 핀 연결 확인 |

---

## 부록 A. bin 목록

모든 bin 은 리셋으로 시작하므로 **bin 하나만 따로 실행·판정 가능**합니다.

| bin | 이름 | 시작 | 끝 | 벡터 수 | 내용 |
|:---:|---|---:|---:|---:|---|
| 0 | reset_idle | 0 | 43 | 44 | 리셋 후 대기 |
| 1 | spi_loopback_zero | 44 | 199 | 156 | SPI 기본 동작 |
| 2 | read_without_acc | 200 | 3,099 | 2,900 | ACC 없이 READ → 무시되는지 |
| 3 | all_ones_9 | 3,100 | 6,167 | 3,068 | 1.0 × 1.0 × 9 |
| 4 | single_element_k | 6,168 | 33,683 | 27,516 | k = 0..8 원소 하나씩 |
| 5 | overwrite_and_rerun | 33,684 | 37,967 | 4,284 | 덮어쓰고 재실행 |
| 6 | double_read | 37,968 | 41,331 | 3,364 | READ 두 번 |
| 7 | sign_relu_cancel | 41,332 | 50,511 | 9,180 | 부호 / ReLU / 상쇄 |
| 8 | special_values | 50,512 | 62,747 | 12,236 | NaN, Inf, 0, 최대값 |
| 9 | rounding | 62,748 | 71,927 | 9,180 | 반올림 |
| 10 | exp_diff_large | 71,928 | 74,995 | 3,068 | 지수 차이 큰 덧셈 |
| 11 | modes | 74,996 | 93,343 | 18,348 | E4M3 / E5M2 4조합 |
| 12 | random_mac | 93,344 | 154,475 | 61,132 | 랜덤 20세트 |
| 13 | min_timing_K2_G3 | 154,476 | 156,000 | 1,525 | 최소 SPI 타이밍 |
| 14 | slow_timing_K16_G32 | 156,001 | 168,140 | 12,140 | 느린 SPI 타이밍 |
| 15 | ignored_cmds_mosi_high | 168,141 | 171,640 | 3,500 | 미정의 명령 무시 |
| 16 | negative_inputs_all_pos | 171,641 | 183,876 | 12,236 | 음수 입력 |
| | **합계** | | | **183,877** | |

---

## 부록 B. 칩 명령어 요약

SPI 프레임 16비트, MSB 먼저:

```
 15  14  13 │ 12 ............ 5 │ 4 ...... 1 │  0
   cmd(3)   │     data8 (8)     │  addr (4)  │ mode
```

| 명령 | cmd | 예시 | 동작 | DONE |
|---|:---:|:---:|---|---|
| LOAD_W | 0 | 0x0700 | W[addr] ← data8 | 1클럭 후 1 |
| LOAD_I | 1 | 0x2700 | I[addr] ← data8 | 1클럭 후 1 |
| COMPUTE | 2 | 0x4000 | 생존 확인용 (연산 없음) | 즉시 1 |
| ACC | 3 | 0x6000 | 9사이클 누산 | 누산 끝나면 1 |
| READ | 4 | 0x8000 | ReLU + 양자화 → 송신 버퍼 (ACC 후에만) | 1 |
| (미정의) | 5, 6 | | 무시 | 0 유지 |
| NOP | 7 | 0xE000 | 더미 프레임 → MISO 로 결과 읽기 | 0 |

mode: 0 = E4M3, 1 = E5M2. MAC 1회 = LOAD_W ×9 → LOAD_I ×9 → ACC → READ → NOP(읽기).
SPI 타이밍 파라미터: K = SCLK 반주기(기본 4 사이클), G = 프레임 간 SS high(기본 8 사이클).

---

## 부록 C. 자주 쓰는 명령어

### WSL — 벡터 다시 만들기

```bash
cd ~/test_vector
./run_all.sh                      # 생성 → RTL/GLS 시뮬 → MATCH 확인 → vectors.bin
```

### WSL → Windows 로 복사

```bash
cp ~/test_vector/out/vectors.bin ~/test_vector/vec/stim_bins.csv \
   "/mnt/c/JEHEE_Google_Drive/MPLAB/07.기타/MyChips/testvector/"
```

### Windows → Jetson 으로 복사

```bash
scp vectors.bin stim_bins.csv nano@<젯슨IP>:~/scan7/
```

### Jetson — 실행 (Python 2 가 아니라 반드시 python3)

```bash
sudo python3 scan7_vec_test.py
```

### 실측 로그 판정 (WSL)

```bash
python3 vec/compare_log.py out/expect_gls.txt measured_chip01.txt vec/stim_bins.csv chip01 >> yield.csv
```

---

*테스트벤치: [`7cell/`](../7cell/README.md) — `scan7_selftest.py` (10장 bring-up 자가진단), `scan7_vec_test.py` (전체 벡터 재생 + compare_log 형식 로그), `dryrun/` (PC 에서 하드웨어 없이 검증). 남은 일: 12장 1~6번 실물 확인*
