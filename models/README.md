---
license: apache-2.0
tags:
  - object-detection
  - picodet
  - paddledetection
  - tflite
  - edge
  - raspberry-pi
  - wildlife
  - predator-detection
  - animal-detection
  - agriculture
  - farm
  - poultry
  - coyote
  - fox
  - raptor
  - bird-of-prey
  - camera-trap
  - camera-module-3
  - conservation
pipeline_tag: object-detection
base_model: PaddlePaddle/PaddleDetection
---

# SpotPredator — PicoDet Farm Predator Detector (TFLite)

`predator_picodet_s640_fp16.tflite` is a fine-tuned **PicoDet** object-detection model that
runs on-device on a Raspberry Pi Zero 2 W to detect common farm predators in real time.
It is the vision model behind **SpotPredator**, a solar-powered, LoRa-linked field device
that watches over free-range poultry and alerts a display station when a predator appears.

🔗 **Full project (hardware, enclosure, code, deployment):**
[github.com/JZVince/spotpredator](https://github.com/JZVince/spotpredator)

- **Task:** object detection
- **Base model:** PicoDet (PaddlePaddle / PaddleDetection), fine-tuned
- **Format:** TensorFlow Lite, FP16 quantized (`fp16`)
- **Input:** 640 × 640 RGB
- **Runtime:** `tflite_runtime` / `ai-edge-litert` on Raspberry Pi (CPU, XNNPACK)
- **Detection classes:** `coyote`, `fox`, `raptor` (predators only)
- Non-predators (background, people, poultry) are **negative training instances** — they
  carry no label and produce no detection.

> **Why PicoDet (was YOLO11n):** the earlier SpotPredator model was fine-tuned from Ultralytics
> YOLO11 (AGPL-3.0). This model moves to PicoDet (Apache-2.0) for a permissive license — free to
> use, modify, and deploy commercially with attribution — with comparable on-device speed and
> improved precision (fewer false positives) after hard-negative retraining.

## Intended use

Detecting farm predators (coyote, fox, raptor) from a fixed/rotating outdoor camera so a
low-power edge device can trigger local alerts. Designed for **low-resolution, small-object,
edge-CPU** conditions — predators often occupy only 20–40 px in a 1920×1080 frame, so the
capture pipeline crops the sky band and tiles it into 640×640 patches before inference.

**Out of scope / limitations**
- Trained for a specific set of North-American farm predators; not a general wildlife detector.
- Small, distant, or heavily occluded animals may be missed.
- Performance varies with lighting, weather, and camera exposure.
- Not intended for safety-critical or human-detection use.

## Training data

Fine-tuned on a mix of:
- **Author-collected field images** captured by the SpotPredator camera (Raspberry Pi Camera
  Module 3 / Arducam), representative of the real deployment (low-res, small objects, sky-cropped).
- **[LILA BC](https://lila.science/)** — Labeled Information Library of Alexandria: Biology and
  Conservation (camera-trap / wildlife imagery).
- **[GBIF](https://www.gbif.org/)** — Global Biodiversity Information Facility occurrence media.

> Please review and comply with the individual licenses/terms of the LILA and GBIF media used.
> Author-collected images are owned by the author.

## Model output format

PicoDet is exported **without in-graph NMS**, so the TFLite model has **two outputs**:

- **boxes:** `(1, N, 4)` — decoded `x1, y1, x2, y2` in input-pixel space (0–640), `N ≈ 8500`
- **scores:** `(1, num_classes, N)` — per-class confidence

You threshold + argmax the scores, filter boxes, and apply your own NMS.

## Usage

```python
# On a Raspberry Pi (or any TFLite host)
try:
    from tflite_runtime.interpreter import Interpreter
except ImportError:
    from ai_edge_litert.interpreter import Interpreter
import numpy as np
from PIL import Image

interpreter = Interpreter(model_path="predator_picodet_s640_fp16.tflite")
interpreter.allocate_tensors()
inp = interpreter.get_input_details()
out = interpreter.get_output_details()

# --- Preprocess: 640x640 RGB, /255 then ImageNet mean/std ---
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD  = np.array([0.229, 0.224, 0.225], dtype=np.float32)
img = np.asarray(Image.open("frame.jpg").convert("RGB").resize((640, 640)), dtype=np.float32)
img = ((img / 255.0) - MEAN) / STD
interpreter.set_tensor(inp[0]['index'], img[None].astype(np.float32))
interpreter.invoke()

# --- Two outputs: identify boxes (ends in 4) vs scores by shape ---
box_i = 0 if out[0]['shape'][-1] == 4 else 1
boxes  = interpreter.get_tensor(out[box_i]['index'])[0]        # (N, 4)  x1,y1,x2,y2 in 0..640
scores = interpreter.get_tensor(out[1 - box_i]['index'])[0].T  # (N, num_classes)

labels = ["coyote", "fox", "raptor"]
CONF = 0.7                                                     # deployment threshold
cls_ids, confs = scores.argmax(1), scores.max(1)
for i in np.nonzero(confs >= CONF)[0]:
    print(labels[cls_ids[i]], round(float(confs[i]), 2), boxes[i].tolist())
# (apply your own NMS to de-duplicate overlapping boxes)
```

Confidence threshold used in the SpotPredator deployment: **0.7**.

## License

**Apache-2.0.** This model is fine-tuned from **PicoDet** (PaddlePaddle / PaddleDetection),
which is licensed under Apache-2.0; derivative models inherit Apache-2.0. It is free to use,
modify, and deploy — including commercially and in closed-source or networked services — with
attribution. There is no copyleft/AGPL obligation.

## Attribution

- Base model: **[PicoDet — PaddlePaddle/PaddleDetection](https://github.com/PaddlePaddle/PaddleDetection)** (Apache-2.0)
- Datasets: LILA BC, GBIF, and author-collected imagery
- Project: **SpotPredator** — farm predator detection on the edge

## Citation

```
@software{spotpredator_picodet,
  title  = {SpotPredator: PicoDet Farm Predator Detector (TFLite)},
  author = {JZVince},
  year   = {2026},
  url    = {https://huggingface.co/JZVince/spotpredator-picodet}
}
```
