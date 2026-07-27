import os
import cv2
import time
import random
import string
import secrets
import jwt
import datetime
import threading
import requests
import json
import logging
from flask import Flask, Response, jsonify, request, make_response, render_template_string
from ultralytics import YOLO
from cryptography.hazmat.primitives.asymmetric import ec
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

REGISTRATION_CODE = "".join(random.choices(string.digits, k=6))
BYPASS_PIN = "".join(random.choices(string.digits, k=4))

current_expected_challenge = None

class SystemSecurityStateMachine:
    def __init__(self):
        self.mode = "NORMAL"  # NORMAL, LOCKDOWN
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
            logger.warning(f"🚨 [LOCKDOWN] 시스템 제어 상태 변경: {self.mode}")

state = SystemSecurityStateMachine()

print("\n" + "="*60)
print(f"🔒 [보안 게이트웨이 통합 코어 가동]")
print(f" ▶️ 1차 기기 등록 코드 :  {REGISTRATION_CODE}")
print(f" ▶️ 2차 PIN 암호      :  {BYPASS_PIN}")
print(f" ⚠️ 웹 관제 대시보드   :  https://라즈베리파이IP:8443")
print("="*60 + "\n")

class StreamInferencePipeline:
    def __init__(self):
        self.model = None
        self.frame = None
        self.running = False
        self.lock = threading.Lock()

    def init_yolo_engine(self):
        try:
            self.model = YOLO("yolov8n_320.onnx", task='detect')
            logger.info("⚙️ [AI Core] YOLO v8 ONNX 경량 가속 엔진 세팅 완료.")
        except Exception as e:
            logger.error(f"ONNX 로드 가동 실패, 오리지널 PT 대체 폴백: {e}")
            self.model = YOLO("yolov8n.pt", task='detect')

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
def dispatch_telegram_intrusion_msg(reason):
    if not state.bot_token or not state.chat_id: return
    url = f"https://api.telegram.org/bot{state.bot_token}/sendMessage"
    payload = {
        "chat_id": state.chat_id,
        "text": f"🚨 [보안 게이트웨이 침입 이상 징후]\n사유: {reason}\n복구용 우회 PIN 코드: [{BYPASS_PIN}]\n\n👇 아래 제어판 버튼을 통해 즉각적인 원격 인터록 격발이 가능합니다.",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "🚫 즉시 전면 락다운", "callback_data": "execute_lockdown"},
                    {"text": "🔓 원격 락다운 해제 요청", "callback_data": "request_release_lockdown"}
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
                    
                    reply_url = f"https://api.telegram.org/bot{state.bot_token}/sendMessage"
                    
                    if action == "execute_lockdown":
                        state.trigger_lockdown(True)
                        requests.post(reply_url, json={"chat_id": chat_id, "text": "🚫 [KILL-SWITCH] 락다운이 수동 격발되었습니다. 시스템 권한을 전면 무력화하고 블랙 화면을 강제 송출합니다."})
                    
                    # 👈 [새로운 기능] 텔레그램 메인 창에서 해제 버튼을 눌렀을 때 라즈베리파이가 신호를 캐치하는 루틴
                    elif action == "request_release_lockdown":
                        if state.mode == "NORMAL":
                            requests.post(reply_url, json={"chat_id": chat_id, "text": "ℹ️ 안내: 시스템이 이미 정상 가동(NORMAL) 상태입니다. 해제할 락다운이 존재하지 않습니다."})
                        else:
                            # 웹 페이지 대시보드를 마스터 키 서명 대기 검증창으로 즉각 강제 라우팅 유도하기 위해 알림 사출
                            requests.post(reply_url, json={"chat_id": chat_id, "text": f"🔓 [해제 절차 개시] 보안 검증을 시작합니다. 웹 관제 대시보드 화면(https://라즈베리파이IP:8443)에 접속하여 '마스터 비대칭키 서명 제출' 버튼을 완료해 주십시오."})
                    
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

def generate_jwt_claims(auth_level):
    payload = {"auth_level": auth_level, "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=2)}
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm="HS256")

def extract_jwt_level(token):
    if not token: return "none"
    try:
        data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
        return data.get("auth_level", "none")
    except: return "none"

@app.route('/api/setup_gateway', methods=['POST'])
def api_setup_gateway():
    data = request.json or {}
    if data.get("code", "") != REGISTRATION_CODE:
        return jsonify({"status": "error", "msg": "초기화 인프라 등록 코드가 일치하지 않습니다."}), 401
    try:
        with open(PUBLIC_KEY_FILE, "w") as f: f.write(data.get("public_key", ""))
        config_data = {
            "bot_token": data.get("bot_token", ""), "chat_id": data.get("chat_id", ""),
            "rtsp_url": data.get("rtsp_url", ""), "admin_id": data.get("admin_id", ""), "admin_pw": data.get("admin_pw", "")
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
    if data.get("id") == state.admin_id and data.get("pw") == state.admin_pw:
        resp = make_response(jsonify({"status": "success"}))
        resp.set_cookie("auth_token", generate_jwt_claims("guest"), httponly=True)
        return resp
    return jsonify({"status": "error", "msg": "로그인 자격 증명 실패"}), 401

@app.route('/api/verify_signature', methods=['POST'])
def api_verify_signature():
    global current_expected_challenge # 방금 보낸 난수 변수 가져오기

    data = request.json or {}
    is_recovery = data.get("recovery", False)
    
    if state.mode == "LOCKDOWN" and not is_recovery: 
        return jsonify({"status": "error", "msg": "락다운 상태입니다."}), 403
        
    challenge = data.get("challenge", "")
    signature_hex = data.get("signature", "")
    
    try:
        if current_expected_challenge is None or challenge != current_expected_challenge:
            logger.warning("🚨 [Replay Attack] 난수값이 다름")
            raise InvalidSignature

        current_expected_challenge = None
        
        with open(PUBLIC_KEY_FILE, "rb") as f: 
            pub_key = serialization.load_pem_public_key(f.read())
        pub_key.verify(bytes.fromhex(signature_hex), challenge.encode('utf-8'), ec.ECDSA(hashes.SHA256()))
        
        if is_recovery:
            state.trigger_lockdown(False)
            logger.info("🔓 [Crypto] 마스터 암호키 검증 무결성 입증 완료: 락다운 잠금을 정식 해제합니다.")
            
        resp = make_response(jsonify({"status": "success"}))
        resp.set_cookie("auth_token", generate_jwt_claims("master"), httponly=True)
        return resp
    except InvalidSignature:
        if not is_recovery:
            dispatch_telegram_intrusion_msg("2차 비대칭키 서명 검증 불일치 원천 차단")
        return jsonify({"status": "signature_failed", "msg": "서명 불일치"}), 401
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500

@app.route('/api/verify_pin', methods=['POST'])
def api_verify_pin():
    if state.mode == "LOCKDOWN": return jsonify({"status": "error", "msg": "락다운 상태입니다."}), 403
    data = request.json or {}
    if data.get("pin") == BYPASS_PIN:
        resp = make_response(jsonify({"status": "success"}))
        resp.set_cookie("auth_token", generate_jwt_claims("master"), httponly=True)
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
def generate_secure_stream(level):
    while True:
        if pipeline.frame is None or is_initial_state():
            time.sleep(0.03)
            continue
            
        with pipeline.lock:
            processed_frame = cv2.resize(pipeline.frame.copy(), (640, 360))
        h, w = processed_frame.shape[:2]
        
        if state.mode == "LOCKDOWN" and level != "master":
            processed_frame = np.zeros((h, w, 3), dtype=np.uint8)
            cv2.putText(processed_frame, "⚠️ SYSTEM LOCKDOWN ACTIVE", (40, h//2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        else:
            if level == "none":
                processed_frame = np.zeros((h, w, 3), dtype=np.uint8)
                cv2.putText(processed_frame, "🔒 LOGIN REQUIRED", (40, h//2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            elif level == "guest":
                if pipeline.model is not None:
                    try:
                        results = pipeline.model(processed_frame, classes=[0], conf=0.3, verbose=False)
                        for r in results:
                            if r.boxes is not None:
                                for box in r.boxes:
                                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                                    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
                                    if (x2 > x1) and (y2 > y1):
                                        roi = processed_frame[y1:y2, x1:x2]
                                        processed_frame[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (19, 19), 0)
                    except: pass
                cv2.putText(processed_frame, "SECURE FEED: GUEST (MASKED)", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
            elif level == "master":
                cv2.putText(processed_frame, "SECURE FEED: MASTER (RAW)", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        ret, buffer = cv2.imencode('.jpg', processed_frame)
        if not ret: continue
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

@app.route('/video_feed')
def video_feed():
    level = extract_jwt_level(request.cookies.get("auth_token"))
    return Response(generate_secure_stream(level), mimetype='multipart/x-mixed-replace; boundary=frame')

# =================================================================
# 🌐 [웹 템플릿 및 인터페이스 제어 뷰 계층]
# =================================================================
@app.route('/')
def index():
    if is_initial_state(): return render_setup_wizard()
    level = extract_jwt_level(request.cookies.get("auth_token"))
    
    if state.mode == "LOCKDOWN" and level != "master":
        return render_lockdown_gate()
    if level == "none": return render_login_gate()
    return render_main_dashboard(level)

@app.route('/settings')
def settings_page():
    level = extract_jwt_level(request.cookies.get("auth_token"))
    if level != "master":
        return "<h1>🔒 접근 거부: 마스터 단말 전자서명 소유자만 진입할 수 있습니다.</h1>", 403
        
    return render_template_string(f"""
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
                <input type="text" id="cfg_rtsp" value="{state.rtsp_url}" placeholder="RTSP 스트림 타겟 엔드포인트">
                <button onclick="commitModify('rtsp_url', 'cfg_rtsp')">변경</button>
            </div>
        </div>
        
        <div class="row">
            <label style="font-weight:bold; color:#94a3b8;">2. 텔레그램 API 봇 토큰 (Bot Token)</label>
            <div class="input-group">
                <input type="text" id="cfg_token" value="{state.bot_token}" placeholder="Telegram Bot HTTP API Token">
                <button onclick="commitModify('bot_token', 'cfg_token')">변경</button>
            </div>
        </div>
        
        <div class="row">
            <label style="font-weight:bold; color:#94a3b8;">3. 침입 탐지 경보 수신용 Chat ID</label>
            <div class="input-group">
                <input type="text" id="cfg_chat" value="{state.chat_id}" placeholder="Telegram Target Chat/Group ID">
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
    """)

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
    async function setup(){
        const kp=await window.crypto.subtle.generateKey({name:"ECDSA",namedCurve:"P-256"},true,["sign","verify"]);
        localStorage.setItem("master_private_key",JSON.stringify(Array.from(new Uint8Array(await window.crypto.subtle.exportKey("pkcs8",kp.privateKey)))));
        const pubPem=ab2pem(await window.crypto.subtle.exportKey("spki",kp.publicKey),"PUBLIC KEY");
        fetch('/api/setup_gateway',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
            code:document.getElementById('s_code').value,rtsp_url:document.getElementById('s_rtsp').value,bot_token:document.getElementById('s_tok').value,
            chat_id:document.getElementById('s_chat').value,admin_id:document.getElementById('s_uid').value,admin_pw:document.getElementById('s_pw').value,public_key:pubPem
        })}).then(r=>r.json()).then(d=>{if(d.status==='success')location.reload();else alert("설정 실패");});
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
    level_status = "true" if level == "master" else "false"
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
        <p style="color:#94a3b8; margin:0; font-size:0.8rem;">텔레그램으로 발송된 우회용 일회성 PIN 코드를 입력하면 원본 영상 권한이 허용됩니다.</p>
        <div style="display:flex; gap:5px;"><input type="text" id="l_pin" style="flex:1;padding:8px; background:#0f172a; border:1px solid #475569; color:white; border-radius:4px;" placeholder="PIN 4자리"><button style="padding:8px;background:#ef4444;color:white;border:none;border-radius:6px;font-weight:bold;" onclick="submitBypassPin()">확인</button></div>
    </div>
    
    <button class="btn btn-orange" onclick="tryAccessSettings()">⚙️ 인프라 환경설정 변경</button>
    <button class="btn btn-red" onclick="logout()">🔴 대시보드 로그아웃</button>
    </div></div>

    <script>
    function rawSignToDer(rawSign) {{
        const r = rawSign.slice(0, 32); const s = rawSign.slice(32);
        let rArr = Array.from(r); let sArr = Array.from(s);
        if (rArr[0] > 0x7f) rArr.unshift(0); if (sArr[0] > 0x7f) sArr.unshift(0);
        const der = [0x30, 4 + rArr.length + sArr.length, 0x02, rArr.length, ...rArr, 0x02, sArr.length, ...sArr];
        return btoa(String.fromCharCode.apply(null, der));
    }}

    async function triggerDashboardAuthChain(alreadyMaster){{
        if(alreadyMaster) return;
        
        const pkData = localStorage.getItem("master_private_key");
        if(!pkData) {{
            alert("⚠️ 2차 비대칭키 소유권 인증 실패: 이 브라우저에는 마스터 키가 존재하지 않습니다. 비식별 마스킹을 강제 적용하며, 관리자 텔레그램으로 우회코드를 발송합니다.");
            document.getElementById("pin_gate_panel").style.display = "flex";
            fetch('/api/verify_signature', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{challenge:'', signature:''}})}});
            return;
        }}
        
        const challenge = "Edge_Gate_Auth_" + Date.now();
        const pk = await window.crypto.subtle.importKey("pkcs8", new Uint8Array(JSON.parse(pkData)), {{name: "ECDSA", namedCurve: "P-256"}}, true, ["sign"]);
        const sigBuf = await window.crypto.subtle.sign({{name: "ECDSA", hash: {{name: "SHA-256"}}}}, pk, new TextEncoder().encode(challenge));
        
        const derBase64 = rawSignToDer(new Uint8Array(sigBuf));
        const derBytes = Uint8Array.from(atob(derBase64), c => c.charCodeAt(0));
        const sigHex = Array.from(derBytes).map(b => b.toString(16).padStart(2, '0')).join('');
        
        const sigRes = await fetch('/api/verify_signature', {{
            method: 'POST', headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{challenge: challenge, signature: sigHex}})
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
        const pkData = localStorage.getItem("master_private_key");
        if(!pkData) {{
            alert("🚫 진입 거부: 이 브라우저에는 권한 자산(마스터 암호키)이 없어 환경설정을 수정할 수 없습니다.");
            return;
        }}
        
        const challenge = "Access_Settings_" + Date.now();
        const pk = await window.crypto.subtle.importKey("pkcs8", new Uint8Array(JSON.parse(pkData)), {{name: "ECDSA", namedCurve: "P-256"}}, true, ["sign"]);
        const sigBuf = await window.crypto.subtle.sign({{name: "ECDSA", hash: {{name: "SHA-256"}}}}, pk, new TextEncoder().encode(challenge));
        
        const derBase64 = rawSignToDer(new Uint8Array(sigBuf));
        const derBytes = Uint8Array.from(atob(derBase64), c => c.charCodeAt(0));
        const sigHex = Array.from(derBytes).map(b => b.toString(16).padStart(2, '0')).join('');
        
        const res = await fetch('/api/verify_signature', {{
            method: 'POST', headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{challenge: challenge, signature: sigHex}})
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
            }} else {{ alert("❌ 인증 실패: PIN 번호가 맞지 않습니다."); }}
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
    <p style="color:#fca5a5;">원격 제어 기능에 의해 게이트웨이가 전면 잠금 상태로 전환되었습니다.<br>이 잠금은 오직 마스터 단말기의 전자서명 제출로만 해제할 수 있습니다.</p>
    <hr style="border:1px solid #b91c1c; margin:20px 0;">
    <button style="width:100%; padding:15px; background:#f87171; color:#450a0a; font-weight:bold; font-size:1rem; border:none; border-radius:6px; cursor:pointer;" onclick="releaseLockdown()">🔓 마스터 비대칭키 서명 제출 및 락다운 해제</button>
    </div>
    <script>
    function rawSignToDer(rawSign) {
        const r = rawSign.slice(0, 32); const s = rawSign.slice(32);
        let rArr = Array.from(r); let sArr = Array.from(s);
        if (rArr[0] > 0x7f) rArr.unshift(0); if (sArr[0] > 0x7f) sArr.unshift(0);
        const der = [0x30, 4 + rArr.length + sArr.length, 0x02, rArr.length, ...rArr, 0x02, sArr.length, ...sArr];
        return btoa(String.fromCharCode.apply(null, der));
    }
    async function releaseLockdown(){
        const pkData = localStorage.getItem("master_private_key"); if(!pkData){alert("❌ 해제 거부: 이 단말은 락다운을 해제할 권한이 없습니다.");return;}
        const challenge = "Recover_Lockdown_" + Date.now();
        const pk = await window.crypto.subtle.importKey("pkcs8", new Uint8Array(JSON.parse(pkData)), {name: "ECDSA", namedCurve: "P-256"}, true, ["sign"]);
        const sigBuf = await window.crypto.subtle.sign({name: "ECDSA", hash: {name: "SHA-256"}}, pk, new TextEncoder().encode(challenge));
        
        const derBase64 = rawSignToDer(new Uint8Array(sigBuf));
        const derBytes = Uint8Array.from(atob(derBase64), c => c.charCodeAt(0));
        const sigHex = Array.from(derBytes).map(b => b.toString(16).padStart(2, '0')).join('');
        
        const res = await fetch('/api/verify_signature', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({challenge: challenge, signature: sigHex, recovery: true})
        });
        const d = await res.json();
        if(d.status === "success") { alert("🔓 시스템 복구 완료: 락다운을 해제합니다."); location.reload(); } 
        else { alert("❌ 해제 실패: 서명이 일치하지 않습니다."); }
    }
    </script></body></html>
    """

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8443, debug=False, threaded=True, ssl_context='adhoc')
