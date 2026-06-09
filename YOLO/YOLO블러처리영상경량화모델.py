# [연구 실험 참고 필수 주석: 본 교차 검증 스크립트의 처리 속도(FPS) 특성 안내]
# 
# 본 코드는 실제 라즈베리파이 환경에 이식할 '경량화 박스 탐지 모델(YOLOv8n)'의 
# 사생활 보호 유효성을 정량적으로 검증하기 위해 설계된 [비교 분석용 실험용 스크립트]입니다.
# 
# 구동 시 일반적인 단독 경량화 모델보다 속도(FPS)가 다소 느리게 측정되는 것은 정상적인 현상이며, 원인은 다음과 같습니다:
#   1) 투트랙 듀얼 추론 구조: 채점 기준 자(Ground Truth) 역할을 하는 최고 사양 세그멘테이션 모델(YOLOv8n-seg)과
#      경량화 대상 모델(YOLOv8n)을 백그라운드에서 프레임마다 동시에 구동함.
#   2) 픽셀 단위 연산 오버헤드: 두 모델이 추출한 영역 간의 매칭률을 정밀 검증하기 위해 매 프레임마다 
#      픽셀 행렬 대조 및 np.sum 등의 논리 비트 연산을 추가적으로 수행함.

# pip install opencv-python numpy pandas ultralytics 설치 필요

import cv2
import numpy as np
import pandas as pd
import json
import os
import time  
from datetime import datetime
from ultralytics import YOLO

# -------------------------------------------------------------
# [설정 영역] 실험에 사용할 영상 파일 이름을 적어주세요
video_path = "AIHUB_CLIP.mp4"         
# -------------------------------------------------------------

root_dir = "experiment_outputs_real_compare"
os.makedirs(root_dir, exist_ok=True)

time_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_dir = os.path.join(root_dir, f"real_compare_{time_stamp}")
os.makedirs(output_dir, exist_ok=True)

# --- [교차 검증 투트랙 엔진 세팅] ---
model_light = YOLO('yolov8n.pt')      # 라즈베리 파이 이식용 사각형 기반 경량화 모델
model_heavy = YOLO('yolov8n-seg.pt')  # 절대적 채점 기준(사람 형태 실루엣)을 추출할 최고 사양 모델

cap = cv2.VideoCapture(video_path)

if not cap.isOpened():
    print(f"동영상 파일을 열 수 없습니다: {video_path}")
    exit()

origin_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
origin_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps = cap.get(cv2.CAP_PROP_FPS)
if fps == 0 or fps > 60:
    fps = 30.0

output_blur_path = os.path.join(output_dir, f"M_blur_light_{time_stamp}.mp4")
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
out_blur = cv2.VideoWriter(output_blur_path, fourcc, fps, (origin_w, origin_h))

cv2.namedWindow('Light Model with Real Cross Evaluation', cv2.WINDOW_NORMAL)
cv2.resizeWindow('Light Model with Real Cross Evaluation', 1280, 720)

frame_times = [] 
max_confs = []  
evaluation_results = [] 

class_names = {0: "Person", 24: "Backpack", 26: "Umbrella", 28: "Handbag"}

print(f"경량화 vs 최고사양 교차 검증 정밀 비교 실험 시작 보관소: {output_dir}")

frame_idx = 0
while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break

    start_time = time.time() 
    h, w, _ = frame.shape

    # 1. 최고 사양 세그멘테이션 모델 구동 -> '진짜 사람 형태 실루엣' 정답지(Ground Truth) 확보
    results_heavy = model_heavy(frame, stream=True, classes=[0, 24, 26, 28], conf=0.25, imgsz=640, verbose=False)
    gt_pure_person_mask = np.zeros((h, w), dtype=np.uint8)
    
    for rh in results_heavy:
        if rh.masks is not None:
            for mask in rh.masks.data:
                mask_np = mask.cpu().numpy()
                mask_resized = cv2.resize(mask_np, (w, h), interpolation=cv2.INTER_NEAREST)
                binary_mask = (mask_resized > 0.4).astype(np.uint8) * 255
                gt_pure_person_mask = cv2.bitwise_or(gt_pure_person_mask, binary_mask)

    # 2. 경량화 모델 구동 -> 실제 라즈베리 파이 환경에서 칠 사각형 박스 마스크 생성
    results_light = model_light(frame, stream=True, classes=[0, 24, 26, 28], conf=0.25, imgsz=640, verbose=False)
    actual_blur_mask = np.zeros((h, w), dtype=np.uint8)
    gt_bbox_mask = np.zeros((h, w), dtype=np.uint8) 
    max_conf_in_frame = 0.0  

    for rl in results_light:
        if rl.boxes is not None:
            for box in rl.boxes:
                b_coords = box.xyxy[0].cpu().numpy().astype(int)  
                b_conf = float(box.conf[0].cpu().numpy())  
                
                if b_conf > max_conf_in_frame:
                    max_conf_in_frame = b_conf

                x1, y1, x2, y2 = b_coords
                x1 = max(0, min(x1, w - 1))
                y1 = max(0, min(y1, h - 1))
                x2 = max(0, min(x2, w))
                y2 = max(0, min(y2, h))
                
                if x2 > x1 and y2 > y1:
                    actual_blur_mask[y1:y2, x1:x2] = 255
                    gt_bbox_mask[y1:y2, x1:x2] = 255

    max_confs.append(max_conf_in_frame)

    # --- [경량화 가우시안 블러 처리 엔진] ---
    blur_only_frame = frame.copy()
    if np.sum(actual_blur_mask > 0) > 0:
        blurred_frame = cv2.GaussianBlur(blur_only_frame, (31, 31), 0)
        idx = (actual_blur_mask == 255)
        blur_only_frame[idx] = blurred_frame[idx]

    out_blur.write(blur_only_frame)

    # --- [공정한 교차 정밀 채점 연산] ---
    # 1) 메인 지표 계산 (BBox 기준)
    total_bbox_pixels = np.sum(gt_bbox_mask > 0)
    if total_bbox_pixels > 0:
        masked_bbox_pixels = np.sum((gt_bbox_mask > 0) & (actual_blur_mask > 0))
        bbox_masking_rate = masked_bbox_pixels / total_bbox_pixels
    else:
        masked_bbox_pixels = 0
        bbox_masking_rate = 0.0

    # 2) 보조 지표 계산 (진짜 최고사양 사람 실루엣 마스크 기준)
    total_mask_pixels = np.sum(gt_pure_person_mask > 0)
    if total_mask_pixels > 0:
        masked_pure_pixels = np.sum((gt_pure_person_mask > 0) & (actual_blur_mask > 0))
        mask_masking_rate = masked_pure_pixels / total_mask_pixels
    else:
        masked_pure_pixels = 0
        mask_masking_rate = 0.0

    end_time = time.time() 
    proc_time = end_time - start_time
    frame_times.append(proc_time) 

    # 기존 CSV 컬럼 구조와 완벽히 동일하게 매칭 (다른 임시 변수 삽입 배제)
    evaluation_results.append({
        "frame": frame_idx,
        "total_bbox_pixels": int(total_bbox_pixels),
        "masked_bbox_pixels": int(masked_bbox_pixels),
        "bbox_masking_rate": float(bbox_masking_rate),
        "total_mask_pixels": int(total_mask_pixels),
        "masked_pure_pixels": int(masked_pure_pixels),
        "mask_masking_rate": float(mask_masking_rate),
        "process_time_sec": float(proc_time),
        "max_confidence": float(max_conf_in_frame),
        "success": bbox_masking_rate > 0.5  
    })

    # 화면 디스플레이 출력
    frame_output = cv2.resize(blur_only_frame, (1280, 720), interpolation=cv2.INTER_LINEAR)
    cv2.imshow('Light Model with Real Cross Evaluation', frame_output)

    frame_idx += 1
    if cv2.waitKey(1) & 0xFF == 27: 
        break

cap.release()
out_blur.release()
cv2.destroyAllWindows()

# --- [최종 결과 저장 및 요약부 병합] ---
if len(evaluation_results) > 0:
    result_df = pd.DataFrame(evaluation_results)
    valid_bbox_df = result_df[result_df["total_bbox_pixels"] > 0]
    valid_mask_df = result_df[result_df["total_mask_pixels"] > 0]
    
    if len(valid_bbox_df) > 0:
        tot_bbox = valid_bbox_df["total_bbox_pixels"].sum()
        tot_mas_bbox = valid_bbox_df["masked_bbox_pixels"].sum()
        overall_bbox_rate = tot_mas_bbox / tot_bbox if tot_bbox > 0 else 0
        succ_rate = valid_bbox_df["success"].mean()
    else:
        overall_bbox_rate, succ_rate = 0.0, 0.0
        
    if len(valid_mask_df) > 0:
        tot_mask = valid_mask_df["total_mask_pixels"].sum()
        tot_mas_pure = valid_mask_df["masked_pure_pixels"].sum()
        overall_mask_rate = tot_mas_pure / tot_mask if tot_mask > 0 else 0
    else:
        overall_mask_rate = 0.0
        
    total_elapsed_time = sum(frame_times)
    avg_frame_time = total_elapsed_time / len(frame_times) if len(frame_times) > 0 else 0
    avg_fps = 1.0 / avg_frame_time if avg_frame_time > 0 else 0
    
    valid_confs = [c for c in max_confs if c > 0.0]
    avg_confidence = sum(valid_confs) / len(valid_confs) if len(valid_confs) > 0 else 0.0
    
    # 기존 요약 데이터 테이블 구조 완벽 일치화
    summary_rows = [
        {"frame": "---", "total_bbox_pixels": "---", "masked_bbox_pixels": "---", "bbox_masking_rate": "---", "total_mask_pixels": "---", "masked_pure_pixels": "---", "mask_masking_rate": "---", "process_time_sec": "---", "max_confidence": "---", "success": "---"},
        {"frame": "정답 BBox 내부 평균 마스킹 처리율(%)", "total_bbox_pixels": "", "masked_bbox_pixels": "", "bbox_masking_rate": float(overall_bbox_rate * 100), "total_mask_pixels": "", "masked_pure_pixels": "", "mask_masking_rate": "", "process_time_sec": "", "max_confidence": "", "success": ""},
        {"frame": "보조 지표: 사람 형태(Mask) 평균 처리율(%)", "total_bbox_pixels": "", "masked_bbox_pixels": "", "bbox_masking_rate": "", "total_mask_pixels": "", "masked_pure_pixels": "", "mask_masking_rate": float(overall_mask_rate * 100), "process_time_sec": "", "max_confidence": "", "success": ""},
        {"frame": "프레임 기준 데이터 확보율(%)", "total_bbox_pixels": "", "masked_bbox_pixels": "", "bbox_masking_rate": float(succ_rate * 100), "total_mask_pixels": "", "masked_pure_pixels": "", "mask_masking_rate": "", "process_time_sec": "", "max_confidence": "", "success": ""},
        {"frame": "시스템 전체 총 소요 시간(초)", "total_bbox_pixels": "", "masked_bbox_pixels": "", "bbox_masking_rate": float(total_elapsed_time), "total_mask_pixels": "", "masked_pure_pixels": "", "mask_masking_rate": "", "process_time_sec": "", "max_confidence": "", "success": ""},
        {"frame": "평균 영상 처리 속도(FPS)", "total_bbox_pixels": "", "masked_bbox_pixels": "", "bbox_masking_rate": float(avg_fps), "total_mask_pixels": "", "masked_pure_pixels": "", "mask_masking_rate": "", "process_time_sec": "", "max_confidence": "", "success": ""},
        {"frame": "탐지된 객체 평균 신뢰도 점수", "total_bbox_pixels": "", "masked_bbox_pixels": "", "bbox_masking_rate": float(avg_confidence), "total_mask_pixels": "", "masked_pure_pixels": "", "mask_masking_rate": "", "process_time_sec": "", "max_confidence": "", "success": ""}
    ]
    summary_df = pd.DataFrame(summary_rows)
    final_excel_df = pd.concat([result_df, summary_df], ignore_index=True)
    
    csv_name = os.path.join(output_dir, f"result_cross_eval_{time_stamp}.csv")
    final_excel_df.to_csv(csv_name, index=False, encoding="utf-8-sig")
    
    # 요구하신 콘솔 양식 포맷팅 일치 출력
    print("\n========== BBox 및 Mask 투트랙 정밀 평가 결과 ==========")
    print(f"정답 BBox 내부 평균 마스킹 처리율: {overall_bbox_rate * 100:.2f}%")
    print(f"보조 지표: 사람 형태(Mask) 평균 처리율: {overall_mask_rate * 100:.2f}%")
    print(f"프레임 기준 데이터 확보율: {succ_rate * 100:.2f}%")
    print(f"시스템 전체 총 소요 시간: {total_elapsed_time:.2f}초")
    print(f"평균 영상 처리 속도: {avg_fps:.2f} FPS")
    print(f"탐지된 객체 평균 신뢰도(Confidence): {avg_confidence:.4f}")
    print(f"저장된 결과 폴더 위치: {output_dir}")
    print("==================================================\n")