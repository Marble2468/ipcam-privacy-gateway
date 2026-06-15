첨부해주신 논문() 내용을 바탕으로 오픈소스 프로젝트의 매력을 극대화할 수 있는 깔끔하고 체계적인 **GitHub README.md** 템플릿을 작성해 보았습니다.

이 프로젝트는 엣지 디바이스 환경에서 보안과 사용성이라는 상충 관계를 훌륭하게 풀어낸 만큼, 기술적 차별점(투트랙 라우팅, 가속 엔진 벤치마크)이 시각적으로 잘 드러나도록 구성했습니다.

---

# 📝 GitHub README.md Template

```markdown
# 🛡️ IP Camera Security Gateway

> **Asymmetric Key Digital Signature Authentication and Real-Time Video Masking Based Multi-Layer Defense Architecture**
> 
> 본 프로젝트는 암호학적 단말 인증과 엣지 기반 실시간 영상 비식별화 기술을 융합하여, 가정용 IP 카메라의 사생활 유출을 원천 차단하면서도 안전한 원격 웹 관제를 보장하는 다층 방어 보안 게이트웨이 시스템입니다.

---

## 🌟 Key Features

* **🔒 투트랙(Two-Track) 동적 라우팅**: 단말의 인증 상태에 따라 네트워크 트래픽을 동적으로 분기합니다.
    * **인가 기기**: 연산 오버헤드 없이 고화질 원본 실시간 영상 스트림(Raw Stream) 제공
    * **비인가 기기**: 세션을 무조건 차단하는 대신, 실시간 AI 객체 탐지 파이프라인을 트리거하여 사람 형태를 난독화한 비식별화 스트림(Masked Stream) 제공 (Fail-Safe)
* **🔑 WebCrypto API 기반 인증**: 웹 표준 WebCrypto API 기반의 비대칭키(ECDSA) 챌린지-리스폰스 메커니즘을 적용하여 지식 기반 인증의 취약점을 보완합니다.
* **⚡ 엣지 최적화 딥러닝 추론**: 자원이 제한된 Raspberry Pi 환경에서 실시간성을 확보하기 위해 경량화 BBox 확장 모델과 하드웨어 가속 프레임워크(ONNX Runtime, Tencent NCNN)를 통합했습니다.
* **📲 심리스한 UX (신뢰 전이 & 킬스위치)**:
    * **PIN 기반 신뢰 전이**: 신규 기기 등록 시 텔레그램/Web Push로 4자리 일회성 PIN을 발송하여 간편하게 권한을 위임합니다.
    * **아웃바운드 롱 폴링 킬스위치**: 방화벽 포트 개방(Port Forwarding) 없이 텔레그램 봇을 통해 실시간 비상 제어 및 시스템 전면 락다운(Blackout) 명령을 하달할 수 있습니다.

---

## 🏗️ System Architecture

### 전체 시스템 구조 (Overall System Architecture)
IP 카메라의 외부망 직접 통신을 완전히 차단(망 격리)하고, 모든 영상 접근을 로컬 보안 게이트웨이(Raspberry Pi) 단에서 통제합니다.


```

[ IP Camera ] ---> (Local Video Stream) ---> [ Raspberry Pi Gateway ]
|                                                 |
X (Blocked WAN)                                   +---> [ 인가 기기 ] ---> Raw Video Stream
v                                                 |
[ Cloud Server ]                                       +---> [ 비인가 기기 ] ---> Masked Stream (YOLO + Blur)

```

### 영상 마스킹 파이프라인 (Video Masking Pipeline)

```

원본 영상 입력 ──> 프레임 분리 ──> YOLO 기반 객체 탐지 ──> 마스킹 영역 확장 ──> 가우시안 블러/모자이크 적용

```

---

## 📊 Performance & Benchmark

### 1. 하드웨어 가속 엔진별 추론 성능 (Raspberry Pi 실측 데이터)
입력 영상의 해상도와 연산 부하 특성에 따른 가속 엔진별 성능 역전 현상을 규명했습니다. 저해상도에서는 **ONNX**가 우세하지만, 1080p 고해상도 환경에서는 ARM Neon 명령어에 최적화된 **NCNN** 엔진이 **2배 이상의 FPS 상회 및 안정적인 CPU 점유율(52.56%)**을 보입니다.

| 해상도 | 모델 구분 | 가속 엔진 | 연산 속도 (FPS) | 추론 지연 (ms) | CPU 점유율 (%) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **360p** | Baseline (순수 I/O) | N/A | 30.0 | 4.00 | 20.15% |
| | BBox (경량화) | PyTorch | 9.5 | 103.68 | 59.15% |
| | BBox (경량화) | NCNN | 3.0 | 335.21 | 53.35% |
| | BBox (경량화) | **ONNX** | **14.6** | **65.95** | 94.80% |
| **1080p** | Baseline (순수 I/O) | N/A | 30.0 | 1.18 | 19.55% |
| | BBox (경량화) | PyTorch | 1.1 | 897.21 | 52.21% |
| | BBox (경량화) | **NCNN** | **2.8** | **352.30** | **52.56%** |
| | BBox (경량화) | ONNX | 1.4 | 704.34 | 94.79% |

### 2. 프라이버시 보호 성능 평가
픽셀 단위 세그멘테이션 모델 대비 제안하는 BBox 확장 모델은 **평균 99.65%의 우수한 마스킹 처리율**을 기록하여 객체 누락 없는 정밀한 사생활 방어를 달성했습니다. 경량화를 거쳤음에도 객체탐지 신뢰도는 원본 헤비 모델과 단 0.0028 수준의 미미한 오차만을 보입니다.

* **BBox 마스킹 처리율**: 평균 **99.65%** (최고 100%)
* **사람 형태 차단율**: 평균 **97.29%**
* **객체 탐지 신뢰도**: 평균 **0.8630** (원본 모델 0.8658 대비 뛰어난 정확성 유지)

---

## 🛠️ Tech Stacks

* [cite_start]**Hardware**: Raspberry Pi, TP-Link Tapo C100 [cite: 100]
* [cite_start]**OS**: Raspberry Pi OS [cite: 100]
* [cite_start]**Language**: Python [cite: 100]
* [cite_start]**Core Libraries**: OpenCV, NumPy, Pandas [cite: 100]
* [cite_start]**AI Frameworks**: Ultralytics YOLOv8, ONNX Runtime, Tencent NCNN [cite: 48, 49, 103]
* [cite_start]**Protocols & APIs**: WebCrypto API, Telegram Bot API (Long Polling), W3C Web Push API [cite: 66, 84, 87]

---

## 🚀 Getting Started

*(추후 실제 빌드 및 실행 명령어를 이 영역에 작성하시면 됩니다.)*

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
**문석용** (Seokyong Moon) - 선문대학교 컴퓨터공학부 ([msy96322@sunmoon.ac.kr](mailto:msy96322@sunmoon.ac.kr)) 


* 
**김효성** (Hyoseong Kim) - 선문대학교 컴퓨터공학부 ([hyokim1025@sunmoon.ac.kr](mailto:hyokim1025@sunmoon.ac.kr)) 


* 
**이주영** (Jooyoung Lee) - 선문대학교 컴퓨터공학부 ([alth4512@sunmoon.ac.kr](mailto:alth4512@sunmoon.ac.kr)) 


* 
**이태윤** (Taeyoon Lee) - 선문대학교 컴퓨터공학부 ([yoon5998@sunmoon.ac.kr](mailto:yoon5998@sunmoon.ac.kr)) 



```

---

### 💡 팁
* README에서 수식 부분이 필요하다면 `## 🔑 WebCrypto API 기반 인증` 섹션 밑에 본문의 라우팅 조건 수식을 추가하셔도 좋습니다.
* 아키텍처 다이어그램([그림 1], [그림 2]) 이미지를 프로젝트 폴더 내 `images/` 디렉토리에 저장한 뒤, README 내에 `![System](images/system_architecture.png)` 형태로 임베딩하면 시각적 효과가 더욱 극대화됩니다!

```
