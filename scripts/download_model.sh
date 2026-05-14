#!/bin/bash
# Download pre-trained COCO SSD MobileNet model for TensorFlow Lite

set -e

echo "=== SpotPredator Model Downloader ==="
echo "Downloading pre-trained COCO SSD MobileNet model..."

# Create models directory if it doesn't exist
mkdir -p models

cd models
wget -O coco_ssd_mobilenet.zip \
  "https://storage.googleapis.com/download.tensorflow.org/models/tflite/coco_ssd_mobilenet_v1_1.0_quant_2018_06_29.zip"

echo "Extracting model files..."
unzip -o coco_ssd_mobilenet.zip
rm coco_ssd_mobilenet.zip

# EfficientDet-Lite0 has metadata embedded with labels,
# but we also provide a standalone labelmap for compatibility
cat > labelmap.txt << 'EOF'
person
bicycle
car
motorcycle
airplane
bus
train
truck
boat
traffic light
fire hydrant
stop sign
parking meter
bench
bird
cat
dog
horse
sheep
cow
elephant
bear
zebra
giraffe
backpack
umbrella
handbag
tie
suitcase
frisbee
skis
snowboard
sports ball
kite
baseball bat
baseball glove
skateboard
surfboard
tennis racket
bottle
wine glass
cup
fork
knife
spoon
bowl
banana
apple
sandwich
orange
broccoli
carrot
hot dog
pizza
donut
cake
chair
couch
potted plant
bed
dining table
toilet
tv
laptop
mouse
remote
keyboard
cell phone
microwave
oven
toaster
sink
refrigerator
book
clock
vase
scissors
teddy bear
hair drier
toothbrush
EOF

echo ""
echo "✅ Model downloaded successfully!"
echo "   Location: models/detect.tflite"
echo "   Labels:   models/labelmap.txt"
echo ""
echo "Model: EfficientDet-Lite0 (better accuracy than COCO SSD)"
echo "Predator classes detected:"
echo "  - bird (covers hawks, eagles, falcons)"
echo "  - dog  (covers coyotes, foxes, stray dogs)"
echo "  - cat  (covers stray cats)"
echo ""
echo "Note: When you have a custom YOLO11 model trained, replace"
echo "      models/detect.tflite with your exported model."
echo ""
