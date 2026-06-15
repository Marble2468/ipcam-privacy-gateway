```markdown
# 🛡️ IP Camera Security Gateway

> [cite_start]**Asymmetric Key Digital Signature Authentication and Real-Time Video Masking Based Multi-Layer Defense Architecture** [cite: 1]
> 
> [cite_start]본 프로젝트는 암호학적 단말 인증과 엣지 기반 실시간 영상 비식별화 기술을 융합하여, 가정용 IP 카메라의 사생활 유출을 원천 차단하면서도 안전한 원격 웹 관제를 보장하는 다층 방어 보안 게이트웨이 아키텍처입니다[cite: 12].

---

## 🌟 Key Features

* [cite_start]**🔒 투트랙(Two-Track) 동적 라우팅**: 단말의 자격 증명 신뢰도에 따라 네트워크 트래픽을 동적으로 분기합니다[cite: 61].
    * [cite_start]**인가 기기**: 성공적인 암호학적 서명이 검증된 단말에는 추가적인 연산 오버헤드 없이 고화질 원본 실시간 영상 스트림(Raw Video Stream)을 제공합니다[cite: 62].
    * [cite_start]**비인가 기기 (Fail-Safe)**: 자격 증명이 불완전하거나 비인가된 접근 시, 즉각 세션을 차단하는 대신 내부의 객체 탐지 추론 파이프라인을 트리거하여 사람 형태가 난독화된 비식별화 스트림(Masked Stream)만을 격리된 채널로 제한적 라우팅합니다[cite: 63, 64].
* [cite_start]**🔑 WebCrypto API 기반 인증**: 웹 표준 WebCrypto API 기반의 비대칭키(ECDSA) 챌린지-리스폰스 메커니즘을 적용하여 기존 지식 기반 인증의 취약점을 보완합니다[cite: 28, 66].
* [cite_start]**⚡ 엣지 최적화 딥러닝 추론**: 자원이 제한된 임베디드 엣지(Raspberry Pi) 환경에서 실시간성을 확보하고 자원 고갈을 방지하기 위해, 무거운 세그멘테이션 모델 대신 경량화 BBox 확장 모델과 하드웨어 가속 프레임워크(ONNX Runtime, Tencent NCNN)를 결합했습니다[cite: 76, 77].
* **📲 심리스한 UX (신뢰 전이 & 킬스위치)**:
    * [cite_start]**PIN 기반 신뢰 전이**: 신규 기기 등록 시 텔레그램/Web Push로 무작위 생성된 일회성 4자리 PIN 코드를 발송하여, 복잡한 암호키 복사나 인증서 재발급 없이 편리하게 원본 영상 접근 권한을 위임합니다[cite: 84, 86].
    * [cite_start]**아웃바운드 롱 폴링 킬스위치**: 공유기의 인바운드 포트 포워딩(Port Forwarding)이 필요 없는 아웃바운드 구조를 채택하여 공격 표면을 최소화하고 [cite: 88, 89][cite_start], 텔레그램 대화형 콜백 버튼을 통해 시스템의 모든 스트림 권한을 전면 무력화(Blackout)하는 강력한 최후 방어선을 제공합니다[cite: 91, 92].

---

## 🏗️ System Architecture

### 1. 전체 시스템 구조 (Overall System Architecture)
[cite_start]IP 카메라의 외부망 직접 통신 및 제조사 클라우드 의존을 전면 차단(망 격리)하고, 모든 영상 접근을 로컬 보안 게이트웨이(Raspberry Pi) 단에서 안전하게 통제합니다[cite: 57].


```

[ IP Camera ] ---> (Local Video Stream) ---> [ Raspberry Pi Gateway ]
|                                                 |
X (Blocked WAN)                                   +---> [ 인가 기기 ] ---> Raw Video Stream
v                                                 |
[ Cloud Server ]                                       +---> [ 비인가 기기 ] ---> Masked Stream (YOLO + Blur)

```

### 2. 영상 마스킹 파이프라인 (Video Masking Pipeline)
[cite_start]입력된 원본 영상을 프레임 단위로 분리한 후 경량화 구조의 YOLO 기반 객체 탐지 모델을 거쳐 가우시안 블러/모자이크를 적용합니다[cite: 71, 72, 76].

```

원본 영상 입력 ──> 프레임 분리 ──> YOLO 기반 객체 탐지 ──> 마스킹 영역 확장 ──> 가우시안 블러/모자이크 적용

```

---

## 🔑 Cryptographic Authentication Logic

[cite_start]단말 식별의 무결성을 보장하기 위해 브라우저 보안 컨텍스트 내부에서 독립적인 키 쌍을 생성(비밀키 추출 불가능)하며 [cite: 67][cite_start], 다음과 같은 서명 검증 식을 바탕으로 라우팅을 분기합니다[cite: 68].

$$Routing Result = \begin{cases} Raw Stream & \text{if } Verify(PK_{master}, Sign) = R \\ Masked Stream & \text{if } Verify(PK_{master}, Sign) \neq R \end{cases}$$

* [cite_start]$R$: 게이트웨이가 송신하는 임의의 난수 챌린지 [cite: 68]
* [cite_start]$PK_{master}$: 인가된 마스터 단말의 공개키 [cite: 68]

---

## 📊 Performance & Benchmark

### 1. 하드웨어 가속 엔진별 벤치마크 (Raspberry Pi OS 실측 데이터)
[cite_start]입력 영상의 해상도와 연산 집약도에 따라 범용 엔진인 ONNX Runtime과 모바일 CPU에 특화된 NCNN 간의 유의미한 성능 역전 현상이 규명되었습니다[cite: 122]. [cite_start]저해상도(360p)에서는 ONNX가 우세하지만 [cite: 120][cite_start], **1080p 고해상도 환경에서는 ARM Neon 명령어에 최적화된 NCNN 엔진이 ONNX 대비 2배 이상의 FPS를 기록하며 CPU 점유율 또한 52.56% 수준으로 안정적으로 억제**합니다[cite: 122, 125].

| 해상도 | 모델 구분 | 가속 엔진 | 연산 속도 (FPS) | 추론 지연 (ms) | CPU 점유율 (%) |
| :---: | :--- | :---: | :---: | :---: | :---: |
| **360p** | Baseline (순수 I/O) | N/A | 30 | 4 | 20.15 |
| | Seg (일반 모델) | PyTorch | 6.3 | 159.99 | 54.76 |
| | Seg (일반 모델) | NCNN | 2.1 | 477.12 | 51.67 |
| | Seg (일반 모델) | ONNX | 9.2 | 108.52 | 83.77 |
| | BBox (경량화 모델) | PyTorch | 9.5 | 103.68 | 59.15 |
| | BBox (경량화 모델) | NCNN | 3 | 335.21 | 53.35 |
| | BBox (경량화 모델) | **ONNX** | **14.6** | **65.95** | **94.8** |
| **1080p** | Baseline (순수 I/O) | N/A | 30 | 1.18 | 19.55 |
| | Seg (일반 모델) | PyTorch | 0.6 | 1933.23 | 47.44 |
| | Seg (일반 모델) | NCNN | 1.4 | 1040.91 | 46.19 |
| | Seg (일반 모델) | ONNX | 0.7 | 1597.95 | 74.62 |
| | BBox (경량화 모델) | PyTorch | 1.1 | 897.21 | 52.21 |
| | BBox (경량화 모델) | **NCNN** | **2.8** | **352.30** | **52.56** |
| | BBox (경량화 모델) | ONNX | 1.4 | 704.34 | 94.79 |

### 2. 비식별화 및 프라이버시 보호 성능
[cite_start]픽셀 단위 세그멘테이션 모델 대비 본 연구의 경량화 BBox 확장 모델은 탐지된 좌표 영역을 강제 확장하는 알고리즘적 이점 덕분에 **평균 99.65%의 우수한 BBox 마스킹 처리율**을 기록하였습니다[cite: 129, 130]. [cite_start]모델 파라미터를 경량화하였음에도 불구하고 객체 탐지 신뢰도는 원본 헤비 모델과 단 0.0028 수준의 극히 미미한 차이만을 보이며 뛰어난 정확성을 안정적으로 유지했습니다[cite: 138, 139].

* [cite_start]**BBox 마스킹 처리율**: 평균 **99.65%** (최고 100%) [cite: 142]
* [cite_start]**사람 형태 차단율**: 평균 **97.29%** (최고 99.88%) [cite: 142]
* [cite_start]**객체 탐지 신뢰도 (0~1)**: 평균 **0.8630** (원본 모델: 0.8658) [cite: 142]

---

## 🛠️ Tech Stacks

* [cite_start]**Hardware**: Raspberry Pi, TP-Link Tapo C100 [cite: 100]
* [cite_start]**OS**: Raspberry Pi OS [cite: 100]
* [cite_start]**Language**: Python [cite: 100]
* [cite_start]**Core Libraries**: OpenCV, NumPy, Pandas [cite: 100]
* [cite_start]**AI Frameworks**: Ultralytics YOLO 기반 모델, ONNX Runtime, Tencent NCNN [cite: 48, 49, 100]
* [cite_start]**Protocols & APIs**: WebCrypto API, Telegram Bot API (Long Polling), W3C Web Push API [cite: 66, 84, 87]

---

## 🚀 Getting Started

```bash
# Clone the repository
git clone [https://github.com/your-username/ip-camera-security-gateway.git](https://github.com/your-username/ip-camera-security-gateway.git)
cd ip-camera-security-gateway

# Install dependencies
pip install -r requirements.txt

```

---

## 👥 Authors & Affiliation

* 
**이택** (Taek Lee) - 선문대학교 컴퓨터공학부 교수 ([comtaek76@sunmoon.ac.kr](mailto:comtaek76@sunmoon.ac.kr)) 


* 
**문석용** (Seokyong Moon) - 선문대학교 컴퓨터공학부 학부생 ([msy96322@sunmoon.ac.kr](mailto:msy96322@sunmoon.ac.kr)) 


* 
**김효성** (Hyoseong Kim) - 선문대학교 컴퓨터공학부 학부생 ([hyokim1025@sunmoon.ac.kr](mailto:hyokim1025@sunmoon.ac.kr)) 


* 
**이주영** (Jooyoung Lee) - 선문대학교 컴퓨터공학부 학부생 ([alth4512@sunmoon.ac.kr](mailto:alth4512@sunmoon.ac.kr)) 


* 
**이태윤** (Taeyoon Lee) - 선문대학교 컴퓨터공학부 학부생 ([yoon5998@sunmoon.ac.kr](mailto:yoon5998@sunmoon.ac.kr)) 



```

```
