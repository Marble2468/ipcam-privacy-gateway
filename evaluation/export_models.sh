#!/bin/bash
# Build the model files used by the gateway and by evaluate_masking.py.
# Run on the Raspberry Pi in the folder the gateway / evaluation run from.
# Made with ultralytics 8.4.61; yolov8n.pt and yolov8n-seg.pt are downloaded
# by ultralytics if they are not already there.
set -e

# Gateway detector (gateway/app.py) and the light model in evaluate_masking.py.
# Square 320x320 input: the gateway's 640x360 frame is letterboxed into it.
yolo export model=yolov8n.pt format=onnx imgsz=320
mv yolov8n.onnx yolov8n_320.onnx

# Table 4 (benchmark_engines.py): every engine gets the same input, a 16:9 frame
# letterboxed with minimal padding: 192x320 for 360p, 640x1088 for 1080p.
for m in yolov8n yolov8n-seg; do
    for size in 192,320 640,1088; do
        tag=${size/,/x}
        yolo export model=$m.pt format=onnx imgsz=$size
        mv $m.onnx ${m}_${tag}.onnx
        yolo export model=$m.pt format=ncnn imgsz=$size
        rm -rf ${m}_${tag}_ncnn_model
        mv ${m}_ncnn_model ${m}_${tag}_ncnn_model
    done
done

# Reference segmentation in evaluate_masking.py.
# 384x640 input fits a 640x360 frame with 12 px padding top and bottom.
yolo export model=yolov8n-seg.pt format=ncnn imgsz=384,640
rm -rf yolov8n-seg_384x640_ncnn_model
mv yolov8n-seg_ncnn_model yolov8n-seg_384x640_ncnn_model
