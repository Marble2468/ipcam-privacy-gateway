#https://github.com/opencv/opencv 이 링크에서 haarcascade_frontalface_default.xml 파일 다운
import numpy as np
import cv2

# XML 파일 로드
xml_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
face_cascade = cv2.CascadeClassifier(xml_path)

# 파일 로드 실패 시 예외 처리
if face_cascade.empty():
    # 내장 경로로 실패할 경우 현재 폴더의 파일을 시도
    face_cascade = cv2.CascadeClassifier('haarcascade_frontalface_default.xml')

cap = cv2.VideoCapture(0)
cap.set(3, 640)
cap.set(4, 480)

# 마지막으로 탐지된 얼굴 위치를 저장할 변수 (x, y, w, h)
last_face = None
# 얼굴이 사라진 후 유지할 프레임 수 (예: 10프레임 동안 유지)
lost_frame_count = 0
MAX_LOST_FRAMES = 10

while True:
    ret, frame = cap.read()
    if not ret:
        break
        
    frame = cv2.flip(frame, 1)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # 파라미터 조정: minNeighbors를 낮춰 탐지율 상승
    faces = face_cascade.detectMultiScale(gray, 1.1, 4)
    
    current_faces = []

    if len(faces) > 0:
        # 새로운 얼굴이 탐지되면 위치 업데이트 및 카운트 초기화
        last_face = faces[0]
        lost_frame_count = 0
        current_faces = faces
    elif last_face is not None and lost_frame_count < MAX_LOST_FRAMES:
        # 얼굴을 놓쳤지만 아직 유지 기간 내에 있는 경우
        lost_frame_count += 1
        current_faces = [last_face]
    else:
        # 완전히 놓친 경우
        last_face = None
        lost_frame_count = 0

    # 모자이크 처리 루프
    for (x, y, w, h) in current_faces:
        try:
            # 탐지된 얼굴 영역 crop
            face_img = frame[y:y+h, x:x+w]
            
            # 모자이크 강도 조절 (0.04를 조절하여 격자 크기 변경 가능)
            small = cv2.resize(face_img, dsize=(0, 0), fx=0.05, fy=0.05)
            mosaic = cv2.resize(small, (w, h), interpolation=cv2.INTER_AREA)
            
            frame[y:y+h, x:x+w] = mosaic
        except Exception as e:
            # 좌표가 화면 밖으로 나가는 등 예외 발생 시 무시
            pass

    cv2.imshow('Face Mosaic - ESC to Exit', frame)
        
    k = cv2.waitKey(30) & 0xff
    if k == 27:
        break

cap.release()
cv2.destroyAllWindows()