---
license: agpl-3.0
tags:
  - object-detection
  - yolo
  - yolo11
  - yolo11n
  - yolov11
  - yolo11-nano
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
  - conservation
library_name: ultralytics
pipeline_tag: object-detection
base_model: Ultralytics/YOLO11
---

# SpotPredator — YOLO11n Farm Predator Detector (TFLite)

`predator_v2_fp16.tflite` is a fine-tuned **YOLO11 nano** object-detection model that
runs on-device on a Raspberry Pi Zero 2 W to detect common farm predators in real time.
It is the vision model behind **SpotPredator**, a solar-powered, LoRa-linked field device
that watches over free-range poultry and alerts a display station when a predator appears.

🔗 **Full project (hardware, enclosure, code, deployment):**
[github.com/JZVince/spotpredator](https://github.com/JZVince/spotpredator)

- **Task:** object detection
- **Base model:** Ultralytics YOLO11n (nano), fine-tuned
- **Format:** TensorFlow Lite, FP16 quantized (`fp16`)
- **Input:** 640 × 640 RGB
- **Runtime:** `tflite_runtime` / `ai-edge-litert` on Raspberry Pi (CPU, XNNPACK)
- **Detection classes used in deployment:** `coyote`, `fox`, `raptor`
- **Full label set:** `background`, `poultry`, `predator`, `coyote`, `fox`, `raptor`

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

## Usage

```python
# On a Raspberry Pi (or any TFLite host)
try:
    from tflite_runtime.interpreter import Interpreter
except ImportError:
    from ai_edge_litert.interpreter import Interpreter
import numpy as np

interpreter = Interpreter(model_path="predator_v2_fp16.tflite")
interpreter.allocate_tensors()
inp = interpreter.get_input_details()
out = interpreter.get_output_details()

# image: 640x640x3 RGB, normalized as your pipeline expects
interpreter.set_tensor(inp[0]['index'], image[None].astype(np.float32))
interpreter.invoke()
detections = interpreter.get_tensor(out[0]['index'])  # decode YOLO output (boxes + scores)
```

Confidence threshold used in the SpotPredator deployment: **0.7**.

## License

**AGPL-3.0.** This model is fine-tuned from Ultralytics YOLO11, which is licensed under
AGPL-3.0; derivative models inherit AGPL-3.0. If you use this model as part of a networked
service, the AGPL requires you to make your corresponding source available. For commercial
use without AGPL obligations, obtain an [Ultralytics Enterprise License](https://www.ultralytics.com/license).

## Attribution

- Base model: **[Ultralytics YOLO11](https://github.com/ultralytics/ultralytics)** (AGPL-3.0)
- Datasets: LILA BC, GBIF, and author-collected imagery
- Project: **SpotPredator** — farm predator detection on the edge

## Citation

```
@software{spotpredator_yolo11n,
  title  = {SpotPredator: YOLO11n Farm Predator Detector (TFLite)},
  author = {JZVince},
  year   = {2026},
  url    = {https://huggingface.co/JZVince/predator_v2_fp16}
}
```
