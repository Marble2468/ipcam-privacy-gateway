import cv2
import numpy as np
from ultralytics import YOLO

# 속도가 가장 빠른 YOLOv8 나노 세그멘테이션 모델 사용
model = YOLO('yolov8n-seg.pt')

# -------------------------------------------------------------
video_path = "video1.mp4" 
cap = cv2.VideoCapture(video_path)
# -------------------------------------------------------------

if not cap.isOpened():
    print(f"동영상 파일을 열 수 없습니다: {video_path}")
    exit()

cv2.namedWindow('Perfect Speed Blur', cv2.WINDOW_NORMAL)
cv2.resizeWindow('Perfect Speed Blur', 1280, 720)

frame_count = 0
prev_mask = None  # 직전 마스크 저장용 변수

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break

    frame_count += 1

    # [최적화 1] 화질은 인식률이 보장되는 20%(0.2)로 유지
    scale_factor = 0.2
    frame_low = cv2.resize(frame, (0, 0), fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_LINEAR)
    h, w, _ = frame_low.shape

    # [최적화 2] 2프레임마다 '딱 한 번만' YOLO를 돌려 연산 부담을 50% 단축
    # 홀수 프레임에서는 YOLO를 돌려 정확한 위치를 찾고, 짝수 프레임에서는 직전 마스크를 그대로 재사용합니다.
    # 이 방식은 연산 속도를 엄청나게 올리면서도 깜빡이며 짤리는 현상을 완벽히 막아줍니다.
    if frame_count % 2 == 1 or prev_mask is None:
        results = model(frame_low, stream=True, classes=[0], conf=0.35, imgsz=192, verbose=False)
        combined_mask = np.zeros((h, w), dtype=np.uint8)
        has_mask = False

        for r in results:
            if r.masks is not None:
                has_mask = True
                for mask in r.masks.data:
                    mask_np = mask.cpu().numpy()
                    mask_resized = cv2.resize(mask_np, (w, h), interpolation=cv2.INTER_NEAREST)
                    combined_mask = cv2.bitwise_or(combined_mask, (mask_resized > 0.4).astype(np.uint8) * 255)
        
        # 다음 짝수 프레임을 위해 마스크 기록
        prev_mask = combined_mask if has_mask else None
    else:
        # 짝수 프레임은 무거운 딥러닝을 건너뛰고 직전 마스크 재활용
        combined_mask = prev_mask
        has_mask = prev_mask is not None

    if has_mask:
        # 정수리 주변까지 넉넉하게 덮어주는 직사각형 커널
        dilation_kernel = np.ones((13, 5), np.uint8)
        expanded_mask = cv2.dilate(combined_mask, dilation_kernel, iterations=1)

        # 20% 화질 기준 가장 깔끔한 50 규격의 블러 처리
        blurred_frame = cv2.GaussianBlur(frame_low, (25, 25), 0)
        
        idx = (expanded_mask == 255)
        frame_low[idx] = blurred_frame[idx]

    # 최종 화면은 큰 사이즈로 부드럽게 확대 출력
    frame_output = cv2.resize(frame_low, (1280, 720), interpolation=cv2.INTER_LINEAR)
    cv2.imshow('Perfect Speed Blur', frame_output)

    if cv2.waitKey(1) & 0xFF == 27: 
        break

cap.release()
cv2.destroyAllWindows()
