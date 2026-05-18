import cv2
import numpy as np
from ultralytics import YOLO

# 속도가 가장 빠른 YOLOv8 나노 세그멘테이션 모델 사용
model = YOLO('yolov8n-seg.pt')

cap = cv2.VideoCapture(0)

cv2.namedWindow('Expanded Human Shape Blur', cv2.WINDOW_NORMAL)
cv2.resizeWindow('Expanded Human Shape Blur', 1280, 720)

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break

    frame = cv2.flip(frame, 1)
    h, w, _ = frame.shape

    # imgsz=320으로 낮춰 인식을 빠르게 하고, verbose=False로 터미널 로그 생략
    results = model(frame, stream=True, classes=[0], conf=0.4, imgsz=320, verbose=False)

    # 여러 사람의 형체를 하나로 합치기 위한 마스크 초기화
    combined_mask = np.zeros((h, w), dtype=np.uint8)
    has_mask = False

    for r in results:
        if r.masks is not None:
            has_mask = True
            for mask in r.masks.data:
                mask_np = mask.cpu().numpy()
                mask_resized = cv2.resize(mask_np, (w, h), interpolation=cv2.INTER_NEAREST)
                combined_mask = cv2.bitwise_or(combined_mask, (mask_resized > 0.4).astype(np.uint8) * 255)

    if has_mask:
        # --- [핵심 변경 사항] 마스크 확장(팽창) 처리 ---
        # 확장할 크기를 결정하는 커널 생성 (숫자가 클수록 블러 영역이 사람보다 더 커집니다)
        # (15, 15) 정도로 설정하면 사람 몸 바깥쪽까지 부드럽게 감싸줍니다.
        dilation_kernel = np.ones((15, 15), np.uint8)
        expanded_mask = cv2.dilate(combined_mask, dilation_kernel, iterations=1)
        # -----------------------------------------------

        # 블러 강도를 (151, 151)로 크게 키워 아주 진하게 흐림 처리
        blurred_frame = cv2.GaussianBlur(frame, (151, 151), 0)
        
        # 사람 형태보다 조금 더 확장된 마스크 영역에 진한 블러 적용
        idx = (expanded_mask == 255)
        frame[idx] = blurred_frame[idx]

    cv2.imshow('Expanded Human Shape Blur', frame)

    if cv2.waitKey(1) & 0xFF == 27: # ESC 종료
        break

cap.release()
cv2.destroyAllWindows()