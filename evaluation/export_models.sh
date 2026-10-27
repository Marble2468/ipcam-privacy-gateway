#!/bin/bash
# Build the model files used by the gateway and by evaluate_masking.py.
# Run on the Raspberry Pi in the folder the gateway / evaluation run from.
# Made with ultralytics 8.4.61; yolov8n.pt and yolov8n-seg.pt are downloaded
# by ultralytics if they are not already there.
set -e

# Gateway detector (app_final.py) and the light model in evaluate_masking.py.
# Square 320x320 input: the gateway's 640x360 frame is letterboxed into it.
yolo export model=yolov8n.pt format=onnx imgsz=320
mv yolov8n.onnx yolov8n_320.onnx

# Reference segmentation in evaluate_masking.py.
# 384x640 input fits a 640x360 frame with 12 px padding top and bottom.
yolo export model=yolov8n-seg.pt format=ncnn imgsz=384,640
rm -rf yolov8n-seg_384x640_ncnn_model
mv yolov8n-seg_ncnn_model yolov8n-seg_384x640_ncnn_model
