import os
import cv2
import time
import hmac
import secrets
import jwt
import datetime
import threading
import requests
import json
import logging
from flask import Flask, Response, jsonify, request, make_response
from markupsafe import escape
from werkzeug.security import generate_password_hash, check_password_hash
from ultralytics import YOLO
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.exceptions import InvalidSignature
import numpy as np

# 실무 표준 로깅 바인딩
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# RTSP FFMPEG 패킷 유실 방지 TCP 통제
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(32)

PUBLIC_KEY_FILE = "allowed_public_keys.pem"
CONFIG_FILE = "gateway_config.json"
LOCKDOWN_FILE = "lockdown.flag"  # 있으면 락다운. 재부팅해도 풀리지 않게 파일로 남긴다
RELEASE_WINDOW = 300             # 초. 텔레그램에서 해제를 승인한 뒤 서명을 받는 시간

def random_digits(n):
    return f"{secrets.randbelow(10 ** n):0{n}d}"

# 초기 설정용 등록 코드: 콘솔에만 찍힌다. 5번 틀리면 재시작할 때까지 설정 API를 닫는다.
REGISTRATION_CODE = random_digits(6)
REGISTRATION_MAX_FAILS = 5
registration_fails = 0

# 신뢰 이전 PIN: 침입 알림 때 만들되 PIN_TTL마다 한 번만 만든다. 맞히거나 만료되거나 시도 횟수를 다 쓰면 폐기한다.
PIN_TTL = 300          # 초
PIN_MAX_TRIES = 5
TRUSTED_HOURS = 0.5    # PIN으로 얻는 trusted 세션(원본 영상만) 유효 시간
bypass_pin = None      # {"pin": str, "expires": float, "tries_left": int}
pin_issued_at = 0.0    # 마지막 PIN 발급 시각
auth_lock = threading.Lock()  # 등록 코드·PIN 상태 보호

# 서버가 발급한 서명용 챌린지: nonce -> 만료 시각. 한 번 검증에 쓰면(성공이든 실패든) 지운다.
CHALLENGE_TTL = 60   # 초
MAX_PENDING = 100    # 락다운 중에는 로그인 없이 발급하므로 개수 상한을 둔다
pending_challenges = {}
challenge_lock = threading.Lock()

# 마스킹 모델: Table 4에서 가장 빠른 구성 (evaluation/export_models.sh로 생성)
MASK_MODEL = "yolov8n_192x320_ncnn_model"
MASK_IMGSZ = (192, 320)  # 640x360 프레임을 최소 여백으로 넣는 입력 크기

class SystemSecurityStateMachine:
    def __init__(self):
        self.mode = "LOCKDOWN" if os.path.exists(LOCKDOWN_FILE) else "NORMAL"
        self.release_until = 0.0  # 텔레그램 해제 승인이 유효한 시각
        self.bot_token = ""
        self.chat_id = ""
        self.rtsp_url = ""
        self.admin_id = ""
        self.admin_pw = ""
        self.lock = threading.Lock()
        self._load_config()

    def _load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    cfg = json.load(f)
                    self.bot_token = cfg.get("bot_token", "")
                    self.chat_id = cfg.get("chat_id", "")
                    self.rtsp_url = cfg.get("rtsp_url", "")
                    self.admin_id = cfg.get("admin_id", "")
                    self.admin_pw = cfg.get("admin_pw", "")
                logger.info("💾 [System] 인프라 정적 설정 파일 반영 성공.")
            except Exception as e:
                logger.error(f"설정 파일 로드 예외: {e}")

    def update_config_item(self, key, value):
        with self.lock:
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, 'r') as f:
                        cfg = json.load(f)
                    cfg[key] = value
                    with open(CONFIG_FILE, 'w') as f:
                        json.dump(cfg, f)
                    self._load_config()
                    logger.info(f"⚙️ [Config 패치] 성공: {key} 변경 완료.")
                    return True
                except Exception as e:
                    logger.error(f"설정 개별 수정 중 크리티컬 에러: {e}")
            return False

    def trigger_lockdown(self, active=True):
        with self.lock:
            self.mode = "LOCKDOWN" if active else "NORMAL"
            self.release_until = 0.0
            if active:
                # 서명 키를 바꿔 지금까지 발급한 JWT(master 포함)를 전부 무효로 한다
                app.config['SECRET_KEY'] = secrets.token_hex(32)
                open(LOCKDOWN_FILE, "w").close()
            elif os.path.exists(LOCKDOWN_FILE):
                os.remove(LOCKDOWN_FILE)
            logger.warning(f"🚨 [LOCKDOWN] 시스템 제어 상태 변경: {self.mode}")

state = SystemSecurityStateMachine()
if state.mode == "LOCKDOWN":
    logger.warning(f"🚨 [LOCKDOWN] {LOCKDOWN_FILE}이 있어 락다운 상태로 시작합니다.")

print("\n" + "="*60)
print(f"🔒 [보안 게이트웨이 통합 코어 가동]")
print(f" ▶️ 1차 기기 등록 코드 :  {REGISTRATION_CODE}")
print(f" ⚠️ 웹 관제 대시보드   :  https://라즈베리파이IP:8443")
print("="*60 + "\n")

class StreamInferencePipeline:
    def __init__(self):
        self.model = None
        self.frame = None
        self.running = False
        self.lock = threading.Lock()
        self.infer_lock = threading.Lock()  # ultralytics 예측기는 스레드 안전하지 않음

    def init_yolo_engine(self):
        # 대체 모델로 넘어가지 않는다. 못 불러오면 model은 None이고 게스트 화면은 검게 나간다.
        try:
            model = YOLO(MASK_MODEL, task='detect')
            # 빈 프레임으로 한 번 돌려 둔다: 추론이 안 되면 여기서 실패하고, 첫 프레임 지연도 없어진다
            model(np.zeros((360, 640, 3), dtype=np.uint8), imgsz=MASK_IMGSZ, verbose=False)
            self.model = model
            logger.info(f"[AI Core] 마스킹 모델 로드 완료: {MASK_MODEL}")
        except Exception:
            self.model = None
            logger.exception(f"마스킹 모델 로드 실패: {MASK_MODEL}")

    def start_pipeline(self, rtsp_url):
        if not rtsp_url: return
        self.running = True
        threading.Thread(target=self._capture_loop, args=(rtsp_url,), daemon=True).start()

    def _capture_loop(self, url):
        target_url = url.replace("stream1", "stream2")
        cap = cv2.VideoCapture(target_url, cv2.CAP_FFMPEG)
        if cap: cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        while self.running:
            try:
                if cap and cap.isOpened():
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        time.sleep(0.05)
                        continue
                    with self.lock:
                        self.frame = np.copy(frame)
                else:
                    time.sleep(1.0)
            except Exception as e:
                logger.error(f"영상 스트림 수집 루프 예외: {e}")
                time.sleep(1.0)
        cap.release()

pipeline = StreamInferencePipeline()
if state.rtsp_url:
    pipeline.init_yolo_engine()
    pipeline.start_pipeline(state.rtsp_url)

# =================================================================
# 🛰️ [3. 롱 풀링(Long Polling) 수신 백커널 및 알림 시스템]
# =================================================================
def issue_bypass_pin():
    """새 PIN을 만들어 이전 PIN을 대체한다. 직전 발급 후 PIN_TTL이 안 지났으면 만들지 않고 None.
    (알림을 일부러 반복시켜 새 PIN과 시도 횟수를 계속 받아 가는 대입 공격을 막는다)"""
    global bypass_pin, pin_issued_at
    with auth_lock:
        now = time.time()
        if now - pin_issued_at < PIN_TTL:
            return None
        pin_issued_at = now
        bypass_pin = {"pin": random_digits(6), "expires": now + PIN_TTL, "tries_left": PIN_MAX_TRIES}
        return bypass_pin["pin"]

def dispatch_telegram_intrusion_msg(reason):
    if not state.bot_token or not state.chat_id: return
    pin = issue_bypass_pin()
    if pin:
        pin_line = f"복구용 우회 PIN 코드(1회용, {PIN_TTL // 60}분): [{pin}]"
    else:
        pin_line = f"우회 PIN은 {PIN_TTL // 60}분에 한 번만 발급합니다. 직전에 받은 PIN을 쓰십시오."
    url = f"https://api.telegram.org/bot{state.bot_token}/sendMessage"
    payload = {
        "chat_id": state.chat_id,
        "text": f"🚨 [보안 게이트웨이 침입 이상 징후]\n사유: {reason}\n{pin_line}\n\n👇 아래 제어판 버튼을 통해 즉각적인 원격 인터록 격발이 가능합니다.",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "🚫 즉시 전면 락다운", "callback_data": "execute_lockdown"},
                    {"text": f"🔓 락다운 해제 승인({RELEASE_WINDOW // 60}분)", "callback_data": "request_release_lockdown"}
                ]
            ]
        }
    }
    try: requests.post(url, json=payload, timeout=3)
    except Exception as e: logger.error(f"텔레그램 전송 실패: {e}")

def telegram_long_polling_worker():
    logger.info("📡 [Telegram SDK] 내부망 전용 롱 풀링(Long Polling) 경보 리스너 가동 시작.")
    offset = 0
    
    if state.bot_token:
        try: requests.get(f"https://api.telegram.org/bot{state.bot_token}/deleteWebhook")
        except: pass

    while True:
        if not state.bot_token:
            time.sleep(2.0)
            continue
        try:
            url = f"https://api.telegram.org/bot{state.bot_token}/getUpdates?offset={offset}&timeout=10"
            res = requests.get(url, timeout=12)
            if res.status_code != 200:
                time.sleep(2.0)
                continue
                
            updates = res.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                
                if "callback_query" in update:
                    callback = update["callback_query"]
                    action = callback["data"]
                    cb_id = callback["id"]
                    chat_id = callback["message"]["chat"]["id"]
                    if str(chat_id) != str(state.chat_id):  # 등록된 채팅에서 누른 버튼만 받는다
                        logger.warning(f"등록되지 않은 채팅({chat_id})의 텔레그램 버튼 응답을 무시합니다.")
                        continue

                    reply_url = f"https://api.telegram.org/bot{state.bot_token}/sendMessage"
                    
                    if action == "execute_lockdown":
                        state.trigger_lockdown(True)
                        requests.post(reply_url, json={"chat_id": chat_id, "text": "🚫 [KILL-SWITCH] 락다운이 수동 격발되었습니다. 시스템 권한을 전면 무력화하고 블랙 화면을 강제 송출합니다."})
                    
                    # 해제 승인: 키를 가진 기기가 도난당해도 혼자서는 못 풀도록, 승인 후 RELEASE_WINDOW 안에만 서명 해제를 받는다
                    elif action == "request_release_lockdown":
                        if state.mode == "NORMAL":
                            requests.post(reply_url, json={"chat_id": chat_id, "text": "ℹ️ 안내: 시스템이 이미 정상 가동(NORMAL) 상태입니다. 해제할 락다운이 존재하지 않습니다."})
                        else:
                            state.release_until = time.time() + RELEASE_WINDOW
                            requests.post(reply_url, json={"chat_id": chat_id, "text": f"🔓 [해제 승인] {RELEASE_WINDOW // 60}분 안에 웹 관제 대시보드(https://라즈베리파이IP:8443)에서 '마스터 비대칭키 서명 제출'을 완료해 주십시오."})
                    
                    requests.post(f"https://api.telegram.org/bot{state.bot_token}/answerCallbackQuery", json={"callback_query_id": cb_id})
        except Exception as e:
            time.sleep(2.0)

# 백그라운드 스레드 상시 상주 가동
threading.Thread(target=telegram_long_polling_worker, daemon=True).start()

# =================================================================
# 🌐 [REST API 데이터 제어 라우터 계층]
# =================================================================
def is_initial_state():
    return not os.path.exists(PUBLIC_KEY_FILE) or not state.admin_id

def generate_jwt_claims(auth_level, hours=2):
    payload = {"auth_level": auth_level, "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=hours)}
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm="HS256")

def extract_jwt_level(token):
    if not token: return "none"
    try:
        data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
        return data.get("auth_level", "none")
    except: return "none"

@app.route('/api/setup_gateway', methods=['POST'])
def api_setup_gateway():
    global registration_fails
    # 설치가 끝난 뒤에는 닫는다. 다시 설치하려면 파이에서 PUBLIC_KEY_FILE·CONFIG_FILE을 지우고 재시작
    if not is_initial_state():
        return jsonify({"status": "error", "msg": "이미 초기 설정이 끝났습니다."}), 403
    data = request.json or {}
    with auth_lock:
        # 코드를 바꿔 주는 것만으로는 대입 속도가 줄지 않으므로, 다 틀리면 재시작할 때까지 닫는다
        if registration_fails >= REGISTRATION_MAX_FAILS:
            return jsonify({"status": "error", "msg": "등록 코드를 여러 번 틀려 설정이 잠겼습니다. 게이트웨이를 재시작하십시오."}), 403
        if not hmac.compare_digest(str(data.get("code", "")).encode(), REGISTRATION_CODE.encode()):
            registration_fails += 1
            if registration_fails >= REGISTRATION_MAX_FAILS:
                print(f" ⚠️ 등록 코드 {REGISTRATION_MAX_FAILS}회 실패: 재시작할 때까지 초기 설정이 잠깁니다.", flush=True)
            return jsonify({"status": "error", "msg": "초기화 인프라 등록 코드가 일치하지 않습니다."}), 401

    # 비어 있거나 잘못된 값이 저장되면 설정이 안 닫히거나(관리자 ID 없음) master를 영영 못 얻는다(공개키 오류)
    if not all(isinstance(data.get(k), str) and data[k] for k in ("admin_id", "admin_pw", "public_key")):
        return jsonify({"status": "error", "msg": "관리자 아이디·비밀번호·공개키가 필요합니다."}), 400
    try:
        pub_key = serialization.load_pem_public_key(data["public_key"].encode())
    except ValueError:
        pub_key = None
    if not (isinstance(pub_key, ec.EllipticCurvePublicKey) and isinstance(pub_key.curve, ec.SECP256R1)):
        return jsonify({"status": "error", "msg": "공개키가 P-256 PEM 형식이 아닙니다."}), 400
    try:
        with open(PUBLIC_KEY_FILE, "w") as f: f.write(data.get("public_key", ""))
        config_data = {
            "bot_token": data.get("bot_token", ""), "chat_id": data.get("chat_id", ""),
            "rtsp_url": data.get("rtsp_url", ""), "admin_id": data["admin_id"], "admin_pw": generate_password_hash(data["admin_pw"])
        }
        with open(CONFIG_FILE, "w") as f: json.dump(config_data, f)
        state._load_config()
        pipeline.init_yolo_engine()
        pipeline.start_pipeline(state.rtsp_url)
        return jsonify({"status": "success"})
    except Exception as e: return jsonify({"status": "error", "msg": str(e)}), 500

@app.route('/api/login_idpw', methods=['POST'])
def api_login_idpw():
    if state.mode == "LOCKDOWN": return jsonify({"status": "error", "msg": "시스템이 현재 락다운 상태입니다."}), 403
    data = request.json or {}
    pw = data.get("pw")
    if data.get("id") == state.admin_id and isinstance(pw, str) and check_password_hash(state.admin_pw, pw):
        resp = make_response(jsonify({"status": "success"}))
        resp.set_cookie("auth_token", generate_jwt_claims("guest"), httponly=True)
        return resp
    return jsonify({"status": "error", "msg": "로그인 자격 증명 실패"}), 401

@app.route('/api/get_challenge')
def api_get_challenge():
    # 평소에는 1단계(ID/PW)를 통과한 세션에만 발급. 락다운 중에는 로그인이 막혀 있으므로, 텔레그램 해제 승인 뒤에만 누구에게나 발급
    if state.mode == "LOCKDOWN":
        if time.time() > state.release_until:
            return jsonify({"status": "error", "msg": "텔레그램에서 락다운 해제를 먼저 승인하십시오."}), 403
    elif extract_jwt_level(request.cookies.get("auth_token")) == "none":
        return jsonify({"status": "error", "msg": "먼저 로그인하십시오."}), 401
    now = time.time()
    with challenge_lock:
        for nonce, expires in list(pending_challenges.items()):
            if expires < now:
                del pending_challenges[nonce]
        if len(pending_challenges) >= MAX_PENDING:
            return jsonify({"status": "error", "msg": "잠시 후 다시 시도하십시오."}), 429
        nonce = secrets.token_hex(16)
        pending_challenges[nonce] = now + CHALLENGE_TTL
    return jsonify({"status": "success", "challenge": nonce})

@app.route('/api/verify_signature', methods=['POST'])
def api_verify_signature():
    data = request.json or {}
    is_recovery = data.get("recovery", False)

    if state.mode == "LOCKDOWN" and not is_recovery:
        return jsonify({"status": "error", "msg": "락다운 상태입니다."}), 403
    if state.mode != "LOCKDOWN" and is_recovery:
        return jsonify({"status": "error", "msg": "락다운 상태가 아닙니다."}), 400
    if is_recovery and time.time() > state.release_until:
        return jsonify({"status": "error", "msg": "텔레그램에서 락다운 해제를 먼저 승인하십시오."}), 403
    if not is_recovery and extract_jwt_level(request.cookies.get("auth_token")) == "none":
        return jsonify({"status": "error", "msg": "먼저 로그인하십시오."}), 401

    challenge = str(data.get("challenge", ""))  # 문자열이 아니면(리스트 등) pop에서 500이 나지 않게
    with challenge_lock:
        expires = pending_challenges.pop(challenge, None)  # 일회성: 검증 결과와 관계없이 소비

    try:
        if expires is None or expires < time.time():
            logger.warning("🚨 [Replay Attack] 발급하지 않았거나 이미 쓴(또는 만료된) 챌린지")
            raise InvalidSignature

        # 브라우저(WebCrypto)는 서명을 r||s 64바이트로 준다 -> 여기서 DER로 바꾼다
        raw = bytes.fromhex(str(data.get("signature", "")))
        if len(raw) != 64:
            raise InvalidSignature
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))

        with open(PUBLIC_KEY_FILE, "rb") as f:
            pub_key = serialization.load_pem_public_key(f.read())
        pub_key.verify(der, challenge.encode('utf-8'), ec.ECDSA(hashes.SHA256()))

        if is_recovery:
            state.trigger_lockdown(False)
            logger.info("🔓 [Crypto] 마스터 암호키 검증 무결성 입증 완료: 락다운 잠금을 정식 해제합니다.")
            
        resp = make_response(jsonify({"status": "success"}))
        resp.set_cookie("auth_token", generate_jwt_claims("master"), httponly=True)
        return resp
    except (InvalidSignature, ValueError):  # ValueError: 서명이 hex가 아닌 경우
        if not is_recovery:
            dispatch_telegram_intrusion_msg("2차 비대칭키 서명 검증 불일치 원천 차단")
        return jsonify({"status": "signature_failed", "msg": "서명 불일치"}), 401
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500

@app.route('/api/verify_pin', methods=['POST'])
def api_verify_pin():
    global bypass_pin
    if state.mode == "LOCKDOWN": return jsonify({"status": "error", "msg": "락다운 상태입니다."}), 403
    if extract_jwt_level(request.cookies.get("auth_token")) == "none":
        return jsonify({"status": "error", "msg": "먼저 로그인하십시오."}), 401
    pin = str((request.json or {}).get("pin", ""))
    with auth_lock:
        current = bypass_pin
        if current is None or current["expires"] < time.time():
            bypass_pin = None
            return jsonify({"status": "error", "msg": f"유효한 PIN이 없습니다. 새 PIN은 {PIN_TTL // 60}분에 한 번 발송됩니다."}), 401
        ok = hmac.compare_digest(pin.encode(), current["pin"].encode())
        if ok:
            bypass_pin = None  # 1회용
        else:
            current["tries_left"] -= 1
            if current["tries_left"] <= 0:
                bypass_pin = None  # 잠그지 않고 폐기만 한다(일부러 틀려서 락다운시키는 경로를 막기 위해)
    if ok:
        resp = make_response(jsonify({"status": "success"}))
        # master가 아니라 trusted: 원본 영상만 열리고 설정 변경은 서명(master)이 있어야 한다
        resp.set_cookie("auth_token", generate_jwt_claims("trusted", TRUSTED_HOURS), httponly=True)
        return resp
    return jsonify({"status": "error", "msg": "PIN 번호 불일치"}), 401

@app.route('/api/update_setting', methods=['POST'])
def api_update_setting():
    token = request.cookies.get("auth_token")
    if extract_jwt_level(token) != "master":
        return jsonify({"status": "error", "msg": "설정 변경 권한이 없습니다. 마스터 인증을 통과하십시오."}), 403
        
    data = request.json or {}
    target_key = data.get("key", "")
    target_val = data.get("value", "")
    
    valid_keys = ["rtsp_url", "bot_token", "chat_id", "admin_pw"]
    if target_key not in valid_keys:
        return jsonify({"status": "error", "msg": "허용되지 않은 설정 키 자산입니다."}), 400
    if target_key == "admin_pw":
        if not isinstance(target_val, str) or not target_val:
            return jsonify({"status": "error", "msg": "새 비밀번호가 비어 있습니다."}), 400
        target_val = generate_password_hash(target_val)

    success = state.update_config_item(target_key, target_val)
    if success:
        if target_key == "rtsp_url":
            pipeline.running = False
            time.sleep(0.5)
            pipeline.start_pipeline(state.rtsp_url)
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "msg": "정적 설정 맵 저장 실패"}), 500

@app.route('/api/logout', methods=['POST'])
def api_logout():
    resp = make_response(jsonify({"status": "success"}))
    resp.set_cookie("auth_token", "", expires=0)
    return resp

# =================================================================
# 🎥 [영상 관제 스트리밍 분기 처리]
# =================================================================
def generate_secure_stream(token):
    while True:
        if pipeline.frame is None or is_initial_state():
            time.sleep(0.03)
            continue

        # 등급은 프레임마다 다시 확인한다: 토큰이 만료되거나 락다운으로 무효가 되면 열린 스트림도 바로 바뀐다
        level = extract_jwt_level(token)
        with pipeline.lock:
            processed_frame = cv2.resize(pipeline.frame.copy(), (640, 360))
        h, w = processed_frame.shape[:2]

        if state.mode == "LOCKDOWN":
            processed_frame = np.zeros((h, w, 3), dtype=np.uint8)
            cv2.putText(processed_frame, "⚠️ SYSTEM LOCKDOWN ACTIVE", (40, h//2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        else:
            if level == "none":
                processed_frame = np.zeros((h, w, 3), dtype=np.uint8)
                cv2.putText(processed_frame, "🔒 LOGIN REQUIRED", (40, h//2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            elif level == "guest":
                masked = False
                if pipeline.model is not None:
                    try:
                        # 뷰어마다 따로 추론하고 차례로 처리한다. 뷰어가 늘면 프레임당 한 번 추론해 공유하는 구조로 바꿀 것
                        with pipeline.infer_lock:
                            results = pipeline.model(processed_frame, imgsz=MASK_IMGSZ, classes=[0], conf=0.3, verbose=False)
                        for r in results:
                            if r.boxes is not None:
                                for box in r.boxes:
                                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                                    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
                                    if (x2 > x1) and (y2 > y1):
                                        roi = processed_frame[y1:y2, x1:x2]
                                        processed_frame[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (19, 19), 0)
                        masked = True
                    except Exception:
                        logger.exception("마스킹 추론 실패")
                if masked:
                    cv2.putText(processed_frame, "SECURE FEED: GUEST (MASKED)", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
                else:
                    # fail-closed: 마스킹을 못 했으면 원본 대신 검은 화면
                    processed_frame = np.zeros((h, w, 3), dtype=np.uint8)
                    cv2.putText(processed_frame, "MASKING UNAVAILABLE", (40, h//2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            elif level in ("master", "trusted"):  # trusted = PIN 통과 세션
                cv2.putText(processed_frame, f"SECURE FEED: {level.upper()} (RAW)", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        ret, buffer = cv2.imencode('.jpg', processed_frame)
        if not ret: continue
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

@app.route('/video_feed')
def video_feed():
    return Response(generate_secure_stream(request.cookies.get("auth_token")), mimetype='multipart/x-mixed-replace; boundary=frame')

# =================================================================
# 🌐 [웹 템플릿 및 인터페이스 제어 뷰 계층]
# =================================================================
@app.route('/')
def index():
    if is_initial_state(): return render_setup_wizard()
    level = extract_jwt_level(request.cookies.get("auth_token"))
    
    if state.mode == "LOCKDOWN":
        return render_lockdown_gate()
    if level == "none": return render_login_gate()
    return render_main_dashboard(level)

@app.route('/settings')
def settings_page():
    level = extract_jwt_level(request.cookies.get("auth_token"))
    if level != "master":
        return "<h1>🔒 접근 거부: 마스터 단말 전자서명 소유자만 진입할 수 있습니다.</h1>", 403
        
    # 템플릿 엔진을 거치지 않는다: 설정값에 든 Jinja 문법이 실행되지 않게 하고, 값은 HTML 이스케이프만 한다
    return f"""
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><title>System Config Center</title>
    <style>
        body{{font-family:sans-serif;background:#0f172a;color:#f8fafc;padding:40px;display:flex;flex-direction:column;align-items:center;}}
        .config-box{{background:#1e293b; padding:30px; border-radius:12px; width:550px; display:flex; flex-direction:column; gap:18px; border:1px solid #334155;}}
        .row{{display:flex; flex-direction:column; gap:6px;}}
        .input-group{{display:flex; gap:8px;}}
        input{{flex:1; padding:10px; background:#0f172a; border:1px solid #475569; color:white; border-radius:6px; font-size:0.9rem;}}
        button{{padding:10px 16px; background:#3b82f6; color:white; border:none; border-radius:6px; font-weight:bold; cursor:pointer; font-size:0.9rem;}}
        button:hover{{background:#2563eb;}}
    </style></head>
    <body>
    <h2>⚙️ 인프라 환경설정 동적 제어 콘솔</h2>
    <p style="color:#10b981; margin-top:-5px; margin-bottom:20px; font-weight:bold;">✓ 검증 완료: 마스터 암호키 소유 세션</p>
    
    <div class="config-box">
        <div class="row">
            <label style="font-weight:bold; color:#94a3b8;">1. 비디오 스트림 RTSP 주소</label>
            <div class="input-group">
                <input type="text" id="cfg_rtsp" value="{escape(state.rtsp_url)}" placeholder="RTSP 스트림 타겟 엔드포인트">
                <button onclick="commitModify('rtsp_url', 'cfg_rtsp')">변경</button>
            </div>
        </div>
        
        <div class="row">
            <label style="font-weight:bold; color:#94a3b8;">2. 텔레그램 API 봇 토큰 (Bot Token)</label>
            <div class="input-group">
                <input type="text" id="cfg_token" value="{escape(state.bot_token)}" placeholder="Telegram Bot HTTP API Token">
                <button onclick="commitModify('bot_token', 'cfg_token')">변경</button>
            </div>
        </div>
        
        <div class="row">
            <label style="font-weight:bold; color:#94a3b8;">3. 침입 탐지 경보 수신용 Chat ID</label>
            <div class="input-group">
                <input type="text" id="cfg_chat" value="{escape(state.chat_id)}" placeholder="Telegram Target Chat/Group ID">
                <button onclick="commitModify('chat_id', 'cfg_chat')">변경</button>
            </div>
        </div>
        
        <div class="row">
            <label style="font-weight:bold; color:#94a3b8;">4. 웹 관리 대시보드 로그인 비밀번호</label>
            <div class="input-group">
                <input type="password" id="cfg_pw" placeholder="새로운 접속 패스워드 입력">
                <button onclick="commitModify('admin_pw', 'cfg_pw')">변경</button>
            </div>
        </div>
        
        <hr style="border:1px solid #334155; margin:10px 0;">
        <button style="background:#475569;" onclick="location.href='/'">🔙 실시간 관제 대시보드로 복귀</button>
    </div>

    <script>
    function commitModify(keyName, elementId) {{
        const val = document.getElementById(elementId).value;
        if(!val) {{ alert("❌ 변경할 값을 공백 없이 정확하게 채워주십시오."); return; }}
        
        fetch('/api/update_setting', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{key: keyName, value: val}})
        }}).then(r => r.json()).then(d => {{
            if(d.status === 'success') {{
                alert("✨ 개별 설정 동적 반영 성공: " + keyName + " 자산이 즉시 변경되었습니다.");
            }} else {{
                alert("❌ 반영 거부: " + d.msg);
            }}
        }});
    }}
    </script>
    </body></html>
    """

def render_setup_wizard():
    return """
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><title>Setup Wizard</title>
    <style>body{font-family:sans-serif;background:#0f172a;color:#f8fafc;display:flex;justify-content:center;padding-top:50px;} .card{background:#1e293b;padding:30px;border-radius:12px;width:480px;}</style></head>
    <body><div class="card"><h2>⚙️ 1단계 인프라 초기 설정</h2><hr style="border:1px solid #334155; margin-bottom:15px;">
    <input type="text" id="s_code" style="width:100%;padding:10px;margin-bottom:10px;" placeholder="터미널 매칭 코드 (6자리)">
    <input type="text" id="s_rtsp" style="width:100%;padding:10px;margin-bottom:10px;" placeholder="RTSP 주소 (끝자리 stream1 필수)">
    <input type="text" id="s_tok" style="width:100%;padding:10px;margin-bottom:10px;" placeholder="텔레그램 봇 토큰">
    <input type="text" id="s_chat" style="width:100%;padding:10px;margin-bottom:10px;" placeholder="텔레그램 채팅 ID">
    <input type="text" id="s_uid" style="width:100%;padding:10px;margin-bottom:10px;" placeholder="웹 관리자 아이디">
    <input type="password" id="s_pw" style="width:100%;padding:10px;margin-bottom:20px;" placeholder="웹 관리자 비밀번호">
    <button style="width:100%;padding:12px;background:#3b82f6;color:white;font-weight:bold;border:none;border-radius:6px;cursor:pointer;" onclick="setup()">📦 인프라 구축</button></div>
    <script>
    function ab2pem(obj,label){var str=btoa(String.fromCharCode.apply(null,new Uint8Array(obj)));var res="-----BEGIN "+label+"-----\\n";while(str.length>0){res+=str.substring(0,64)+"\\n";str=str.substring(64);}res+="-----END "+label+"-----";return res;}
    // 개인키는 추출 불가 CryptoKey 객체째로 IndexedDB에 둔다(localStorage는 문자열만 담을 수 있다)
    function keyStore(mode, fn) {
        return new Promise((resolve, reject) => {
            const open = indexedDB.open("edge_gateway", 1);
            open.onupgradeneeded = () => open.result.createObjectStore("keys");
            open.onerror = () => reject(open.error);
            open.onsuccess = () => {
                const req = fn(open.result.transaction("keys", mode).objectStore("keys"));
                req.onsuccess = () => resolve(req.result);
                req.onerror = () => reject(req.error);
            };
        });
    }
    async function setup(){
        // extractable=false: 스크립트가 개인키를 꺼낼 수 없다. 공개키는 이 상태에서도 내보낼 수 있다
        const kp=await window.crypto.subtle.generateKey({name:"ECDSA",namedCurve:"P-256"},false,["sign","verify"]);
        const pubPem=ab2pem(await window.crypto.subtle.exportKey("spki",kp.publicKey),"PUBLIC KEY");
        const d=await (await fetch('/api/setup_gateway',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
            code:document.getElementById('s_code').value,rtsp_url:document.getElementById('s_rtsp').value,bot_token:document.getElementById('s_tok').value,
            chat_id:document.getElementById('s_chat').value,admin_id:document.getElementById('s_uid').value,admin_pw:document.getElementById('s_pw').value,public_key:pubPem
        })})).json();
        if(d.status!=='success'){alert("설정 실패: "+d.msg);return;}
        await keyStore("readwrite", s => s.put(kp.privateKey, "master_private_key"));  // 서버가 공개키를 받은 뒤에만 저장
        localStorage.removeItem("master_private_key");  // 옛 버전이 평문으로 남긴 키
        location.reload();
    }
    </script></body></html>
    """

def render_login_gate():
    return """
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><title>Login Gate</title>
    <style>body{font-family:sans-serif;background:#0f172a;color:#f8fafc;display:flex;justify-content:center;padding-top:80px;} .box{background:#1e293b;padding:25px;border-radius:12px;width:360px; display:flex;flex-direction:column;gap:12px;}</style></head>
    <body>
    <div class="box"><h2>시큐리티 게이트</h2><hr style="border:1px solid #334155;">
    <input type="text" id="l_id" style="padding:10px; background:#0f172a; border:1px solid #475569; color:white; border-radius:6px;" placeholder="관리자 아이디">
    <input type="password" id="l_pw" style="padding:10px; background:#0f172a; border:1px solid #475569; color:white; border-radius:6px;" placeholder="비밀번호">
    <button style="padding:12px;background:#3b82f6;color:white;font-weight:bold;border:none;border-radius:6px;cursor:pointer; font-size:1rem;" onclick="loginIdPw()">🔑 시스템 대시보드 진입</button>
    </div>
    <script>
    function loginIdPw(){
        fetch('/api/login_idpw',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:document.getElementById('l_id').value,pw:document.getElementById('l_pw').value})})
        .then(r=>r.json()).then(d=>{
            if(d.status==='success') { location.href = "/"; } 
            else { alert("❌ 로그인 자격 증명 실패: 아이디 또는 패스워드가 다릅니다."); }
        });
    }
    </script></body></html>
    """

def render_main_dashboard(level):
    # trusted도 서명 시도를 건너뛴다(새로 고칠 때마다 서명 실패 -> 알림 -> 새 PIN이 반복되지 않게)
    level_status = "true" if level in ("master", "trusted") else "false"
    return f"""
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><title>Edge Dashboard</title>
    <style>
        body{{font-family:sans-serif;background:#0f172a;color:#f8fafc;padding:20px;display:flex;flex-direction:column;align-items:center;}}
        .btn{{padding:12px;color:white;font-weight:bold;border:none;border-radius:6px;cursor:pointer; font-size:0.95rem;}}
        .btn-red{{background:#ef4444;}} .btn-blue{{background:#3b82f6;}} .btn-orange{{background:#f97316;}}
    </style></head>
    <body onload="triggerDashboardAuthChain({level_status})">
    <h1>🛡️ 스마트 홈케어 실시간 보안 관제</h1>
    
    <div style="display:flex;gap:30px; margin-top:20px;"><img src="/video_feed" style="width:720px;border-radius:12px;border:3px solid #334155;">
    <div style="background:#1e293b;padding:25px;border-radius:12px;width:300px;display:flex;flex-direction:column;gap:15px;">
    <h3>제어 센터</h3>
    
    <div id="pin_gate_panel" style="display:none; flex-direction:column; gap:10px; background:#2d3748; padding:15px; border-radius:8px; border:2px dashed #f87171;">
        <p style="color:#f87171; margin:0; font-size:0.85rem; font-weight:bold;">⚠️ 비인가 장치 차단 모드 작동 중</p>
        <p style="color:#94a3b8; margin:0; font-size:0.8rem;">텔레그램으로 발송된 일회성 PIN(5분 유효)을 입력하면 30분 동안 원본 영상 권한이 허용됩니다.</p>
        <div style="display:flex; gap:5px;"><input type="text" id="l_pin" style="flex:1;padding:8px; background:#0f172a; border:1px solid #475569; color:white; border-radius:4px;" placeholder="PIN 6자리"><button style="padding:8px;background:#ef4444;color:white;border:none;border-radius:6px;font-weight:bold;" onclick="submitBypassPin()">확인</button></div>
    </div>
    
    <button class="btn btn-orange" onclick="tryAccessSettings()">⚙️ 인프라 환경설정 변경</button>
    <button class="btn btn-red" onclick="logout()">🔴 대시보드 로그아웃</button>
    </div></div>

    <script>
    // 서버가 발급한 챌린지에 서명한다. 서명은 WebCrypto가 주는 r||s 64바이트를 hex로 그대로 보낸다(DER 변환은 서버).
    async function signChallenge(pk) {{
        const c = await (await fetch('/api/get_challenge')).json();
        if(c.status !== "success") {{ alert("❌ 챌린지 발급 실패: " + c.msg); return null; }}
        const sig = await window.crypto.subtle.sign({{name: "ECDSA", hash: {{name: "SHA-256"}}}}, pk, new TextEncoder().encode(c.challenge));
        const sigHex = Array.from(new Uint8Array(sig)).map(b => b.toString(16).padStart(2, '0')).join('');
        return {{challenge: c.challenge, signature: sigHex}};
    }}

    // 설정 마법사가 IndexedDB에 넣어 둔 추출 불가 개인키. 없거나 IndexedDB를 못 쓰면 undefined
    function loadMasterKey() {{
        return new Promise(resolve => {{
            const open = indexedDB.open("edge_gateway", 1);
            open.onupgradeneeded = () => open.result.createObjectStore("keys");
            open.onerror = () => resolve(undefined);
            open.onsuccess = () => {{
                const req = open.result.transaction("keys", "readonly").objectStore("keys").get("master_private_key");
                req.onsuccess = () => resolve(req.result);
                req.onerror = () => resolve(undefined);
            }};
        }});
    }}

    async function triggerDashboardAuthChain(alreadyMaster){{
        if(alreadyMaster) return;

        const pk = await loadMasterKey();
        if(!pk) {{
            alert("⚠️ 2차 비대칭키 소유권 인증 실패: 이 브라우저에는 마스터 키가 존재하지 않습니다. 비식별 마스킹을 강제 적용하며, 관리자 텔레그램으로 우회코드를 발송합니다.");
            document.getElementById("pin_gate_panel").style.display = "flex";
            fetch('/api/verify_signature', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{challenge:'', signature:''}})}});
            return;
        }}

        const signed = await signChallenge(pk);
        if(!signed) return;

        const sigRes = await fetch('/api/verify_signature', {{
            method: 'POST', headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(signed)
        }});
        
        const sigData = await sigRes.json();
        if(sigData.status === "success") {{
            alert("✅ [암호학적 인증 완료] 마스터 기기 전자서명 검증에 성공했습니다. 1080p 원본 스트리밍을 개방합니다.");
            location.reload();
        }} else {{
            alert("🚨 [보안 위협 경고] 기기 도난 및 위조 서명이 감지되었습니다! 마스킹 관제 모드로 차단 격리하며 텔레그램 킬스위치를 전송합니다.");
            document.getElementById("pin_gate_panel").style.display = "flex";
        }}
    }}

    async function tryAccessSettings() {{
        const pk = await loadMasterKey();
        if(!pk) {{
            alert("🚫 진입 거부: 이 브라우저에는 권한 자산(마스터 암호키)이 없어 환경설정을 수정할 수 없습니다.");
            return;
        }}

        const signed = await signChallenge(pk);
        if(!signed) return;

        const res = await fetch('/api/verify_signature', {{
            method: 'POST', headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(signed)
        }});
        
        const data = await res.json();
        if(data.status === "success") {{
            alert("🔓 [서명 소유권 입증 완료] 설정 동적 제어 콘솔로 이동합니다.");
            location.href = "/settings";
        }} else {{
            alert("❌ 서명 검증 실패: 설정 수정을 불허합니다.");
        }}
    }}

    function submitBypassPin() {{
        fetch('/api/verify_pin',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{pin:document.getElementById('l_pin').value}})}})
        .then(r=>r.json()).then(d=>{{
            if(d.status==='success') {{
                alert("🔓 신뢰 권한 위임: PIN 번호가 확인되었습니다. 원본 영상 관제로 즉시 전환합니다.");
                location.reload();
            }} else {{ alert("❌ 인증 실패: " + d.msg); }}
        }});
    }}

    function logout() {{ fetch('/api/logout',{{method:'POST'}}).then(()=>{{location.reload();}}); }}
    </script></body></html>
    """

def render_lockdown_gate():
    return """
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><title>LOCKDOWN LOCK</title>
    <style>body{font-family:sans-serif;background:#450a0a;color:#f8fafc;display:flex;justify-content:center;padding-top:100px;}</style></head>
    <body><div style="background:#7f1d1d; padding:40px; border-radius:12px; text-align:center; border:2px solid #f87171; width:500px;">
    <h1 style="font-size:3rem; margin:0;">🚫</h1><h2 style="margin:10px 0 20px 0;">SYSTEM LOCKED DOWN</h2>
    <p style="color:#fca5a5;">원격 제어 기능에 의해 게이트웨이가 전면 잠금 상태로 전환되었습니다.<br>텔레그램에서 '락다운 해제 승인'을 누른 뒤 5분 안에 마스터 단말기의 전자서명을 제출해야 해제됩니다.</p>
    <hr style="border:1px solid #b91c1c; margin:20px 0;">
    <button style="width:100%; padding:15px; background:#f87171; color:#450a0a; font-weight:bold; font-size:1rem; border:none; border-radius:6px; cursor:pointer;" onclick="releaseLockdown()">🔓 마스터 비대칭키 서명 제출 및 락다운 해제</button>
    </div>
    <script>
    // 대시보드의 signChallenge와 같다(이 페이지는 f-string이 아니라 중괄호가 하나)
    async function signChallenge(pk) {
        const c = await (await fetch('/api/get_challenge')).json();
        if(c.status !== "success") { alert("❌ 챌린지 발급 실패: " + c.msg); return null; }
        const sig = await window.crypto.subtle.sign({name: "ECDSA", hash: {name: "SHA-256"}}, pk, new TextEncoder().encode(c.challenge));
        const sigHex = Array.from(new Uint8Array(sig)).map(b => b.toString(16).padStart(2, '0')).join('');
        return {challenge: c.challenge, signature: sigHex};
    }
    // 대시보드의 loadMasterKey와 같다
    function loadMasterKey() {
        return new Promise(resolve => {
            const open = indexedDB.open("edge_gateway", 1);
            open.onupgradeneeded = () => open.result.createObjectStore("keys");
            open.onerror = () => resolve(undefined);
            open.onsuccess = () => {
                const req = open.result.transaction("keys", "readonly").objectStore("keys").get("master_private_key");
                req.onsuccess = () => resolve(req.result);
                req.onerror = () => resolve(undefined);
            };
        });
    }
    async function releaseLockdown(){
        const pk = await loadMasterKey(); if(!pk){alert("❌ 해제 거부: 이 단말은 락다운을 해제할 권한이 없습니다.");return;}
        const signed = await signChallenge(pk);
        if(!signed) return;

        const res = await fetch('/api/verify_signature', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({...signed, recovery: true})
        });
        const d = await res.json();
        if(d.status === "success") { alert("🔓 시스템 복구 완료: 락다운을 해제합니다."); location.reload(); } 
        else { alert("❌ 해제 실패: " + d.msg); }
    }
    </script></body></html>
    """

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8443, debug=False, threaded=True, ssl_context='adhoc')
