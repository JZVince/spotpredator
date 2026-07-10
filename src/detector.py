"""TensorFlow Lite Detector for Predator Detection - supports YOLO and classification models"""
import logging
import time
import numpy as np
try:
    from tflite_runtime.interpreter import Interpreter
except ImportError:
    from ai_edge_litert.interpreter import Interpreter
from PIL import Image

logger = logging.getLogger(__name__)

# PicoDet ImageNet normalization (RGB), matching PaddleDetection's NormalizeImage transform.
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class PredatorDetector:
    """TFLite detector supporting classification, YOLO11 and PicoDet model formats"""

    def __init__(self, model_path, labels_path, confidence_threshold=0.65):
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold

        # Load labels
        self.labels = self._load_labels(labels_path)

        # Initialize TFLite interpreter
        self.interpreter = Interpreter(model_path=model_path)
        self.interpreter.allocate_tensors()

        # Get input and output details
        self.input_details = self.interpreter.get_input_details()
        self.output_details = self.interpreter.get_output_details()

        # Get expected input size
        self.input_shape = self.input_details[0]['shape']
        self.input_height = self.input_shape[1]
        self.input_width = self.input_shape[2]

        # Detect model format:
        # Classification: single output (1, num_classes) - softmax probabilities
        # YOLO11: single output (1, 4+num_classes, num_boxes)
        # PicoDet: 2 outputs - boxes (1, N, 4) + per-class scores (1, num_classes, N)
        # COCO SSD: 4 outputs
        out_shape = self.output_details[0]['shape']
        if len(self.output_details) == 1 and len(out_shape) == 2:
            self.model_type = 'classification'
        elif len(self.output_details) == 1 and len(out_shape) == 3:
            self.model_type = 'yolo'
        elif len(self.output_details) == 2:
            # PicoDet exported without in-graph NMS: two outputs, already-decoded boxes
            # (1, N, 4) and per-class scores (1, num_classes, N). Identify which is which
            # by the trailing dim (boxes end in 4) so output order doesn't matter.
            self.model_type = 'picodet'
            self._pd_box_idx = 0 if self.output_details[0]['shape'][-1] == 4 else 1
            self._pd_score_idx = 1 - self._pd_box_idx
        else:
            self.model_type = 'coco_ssd'

        self._last_probs = None  # Cache last inference probabilities

        # --- Per-tile speed tracking ---------------------------------------------------------
        # Times the FULL pipeline per detect() call (preprocess + invoke + decode) so we can see
        # the real cost each tile puts on the Pi. last_inference_ms is the most recent call;
        # the rolling stats let us log a periodic average without spamming every tile. Handy for
        # comparing models (YOLO vs NanoDet vs PicoDet) and for spotting slowdowns/thermal throttle.
        self.last_inference_ms = 0.0
        self._time_total_ms = 0.0
        self._time_count = 0
        self._time_max_ms = 0.0
        self._log_every = 100   # log a rolling summary every N detect() calls (0 = never)

        logger.info(f"Detector initialized: {self.input_width}x{self.input_height} ({self.model_type})")
        logger.info(f"Loaded {len(self.labels)} labels")

    def get_speed_stats(self, reset=False):
        """Return rolling inference-speed stats since the last reset:
        {'last_ms', 'avg_ms', 'max_ms', 'count', 'fps'}. Set reset=True to zero the accumulator."""
        count = self._time_count
        avg = (self._time_total_ms / count) if count else 0.0
        stats = {
            'last_ms': round(self.last_inference_ms, 1),
            'avg_ms': round(avg, 1),
            'max_ms': round(self._time_max_ms, 1),
            'count': count,
            'fps': round(1000.0 / avg, 2) if avg > 0 else 0.0,
        }
        if reset:
            self._time_total_ms = 0.0
            self._time_count = 0
            self._time_max_ms = 0.0
        return stats

    def _load_labels(self, labels_path):
        """Load class labels from file"""
        try:
            with open(labels_path, 'r') as f:
                labels = [line.strip() for line in f.readlines()]
            return labels
        except Exception as e:
            logger.error(f"Failed to load labels: {e}")
            return []

    def _preprocess_image(self, image):
        """Preprocess image for model input"""
        if isinstance(image, np.ndarray):
            image = Image.fromarray(image[:, :, ::-1])

        image = image.resize((self.input_width, self.input_height))

        if self.model_type == 'classification':
            # EfficientNetB0 preprocess_input: scale to [-1, 1]
            input_data = np.array(image, dtype=np.float32)
            input_data = (input_data / 127.5) - 1.0
        elif self.model_type == 'yolo':
            input_data = np.array(image, dtype=np.float32) / 255.0
        elif self.model_type == 'picodet':
            # /255 then ImageNet mean/std (RGB) — the `image` is already RGB here.
            input_data = np.array(image, dtype=np.float32) / 255.0
            input_data = ((input_data - _IMAGENET_MEAN) / _IMAGENET_STD).astype(np.float32)
        else:
            input_data = np.array(image, dtype=np.uint8)

        input_data = np.expand_dims(input_data, axis=0)
        return input_data

    def get_all_probabilities(self, image):
        """Run inference and return raw probabilities for all classes.
        Only valid to call immediately after detect() — reuses last inference result."""
        if self._last_probs is not None:
            return self._last_probs
        # Fallback: run inference independently
        input_data = self._preprocess_image(image)
        self.interpreter.set_tensor(self.input_details[0]['index'], input_data)
        self.interpreter.invoke()
        output = self.interpreter.get_tensor(self.output_details[0]['index'])
        probabilities = output[0]
        return {self.labels[i]: float(probabilities[i]) for i in range(len(self.labels))}

    def _decode_classification(self, output, ignore_classes):
        """
        Decode classification model output.
        Output shape: (1, num_classes) - softmax probabilities
        Returns detection if predator class has highest confidence above threshold.
        """
        probabilities = output[0]  # (num_classes,)
        class_id = int(np.argmax(probabilities))
        confidence = float(probabilities[class_id])

        if class_id >= len(self.labels):
            return []

        class_name = self.labels[class_id].lower()

        # Ignore background and ignored classes
        if class_name == 'background':
            return []

        if ignore_classes and class_name in ignore_classes:
            return []

        if confidence < self.confidence_threshold:
            return []

        return [{'class': class_name, 'confidence': confidence}]

    def _decode_yolo(self, output, target_classes):
        """Decode YOLO11 TFLite output tensor"""
        predictions = output[0]  # (4+num_classes, num_boxes)
        detections = []

        for i in range(predictions.shape[1]):
            box = predictions[:4, i]
            class_scores = predictions[4:, i]
            class_id = int(np.argmax(class_scores))
            confidence = float(class_scores[class_id])

            if confidence < self.confidence_threshold:
                continue
            if class_id >= len(self.labels):
                continue

            class_name = self.labels[class_id].lower()
            if target_classes and class_name not in target_classes:
                continue

            cx, cy, w, h = box
            xmin = float(cx - w / 2)
            ymin = float(cy - h / 2)
            xmax = float(cx + w / 2)
            ymax = float(cy + h / 2)

            detections.append({
                'class': class_name,
                'confidence': confidence,
                'box': [xmin, ymin, xmax, ymax]
            })

        return self._nms(detections)

    def _decode_picodet(self, boxes, scores, target_classes):
        """Decode PicoDet output (exported with post_process=True, nms=False).

        boxes:  (N, 4)  already-decoded x1,y1,x2,y2 in input-pixel space (0..640).
        scores: (num_classes, N)  per-class confidence.

        Boxes come out in the tile's own 640x640 pixel space (the tile IS the model
        input size), so no scaling is needed — only clip to the frame. We threshold in
        numpy first (only a handful of the ~8500 anchors survive) so the Python loop and
        the shared class-aware NMS stay cheap on the Pi.
        """
        scores = scores.T                        # (N, num_classes)
        cls_ids = scores.argmax(axis=1)
        confs = scores.max(axis=1)
        W, H = self.input_width, self.input_height

        detections = []
        for i in np.nonzero(confs >= self.confidence_threshold)[0]:
            class_id = int(cls_ids[i])
            if class_id >= len(self.labels):
                continue
            class_name = self.labels[class_id].lower()
            if target_classes and class_name not in target_classes:
                continue
            x1, y1, x2, y2 = boxes[i]
            detections.append({
                'class': class_name,
                'confidence': float(confs[i]),
                'box': [max(0.0, min(float(x1), W)), max(0.0, min(float(y1), H)),
                        max(0.0, min(float(x2), W)), max(0.0, min(float(y2), H))]
            })

        return self._nms(detections)

    def _nms(self, detections, iou_threshold=0.5):
        """Simple non-max suppression"""
        if not detections:
            return []
        detections = sorted(detections, key=lambda x: x['confidence'], reverse=True)
        kept = []
        for det in detections:
            overlap = False
            for kept_det in kept:
                if det['class'] == kept_det['class'] and self._iou(det['box'], kept_det['box']) > iou_threshold:
                    overlap = True
                    break
            if not overlap:
                kept.append(det)
        return kept

    def _iou(self, box1, box2):
        """Compute intersection over union"""
        x1 = max(box1[0], box2[0])
        y1 = max(box1[1], box2[1])
        x2 = min(box1[2], box2[2])
        y2 = min(box1[3], box2[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
        area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
        union = area1 + area2 - inter
        return inter / union if union > 0 else 0

    def _decode_coco_ssd(self, target_classes):
        """Decode COCO SSD 4-output format"""
        boxes = self.interpreter.get_tensor(self.output_details[0]['index'])[0]
        classes = self.interpreter.get_tensor(self.output_details[1]['index'])[0]
        scores = self.interpreter.get_tensor(self.output_details[2]['index'])[0]
        num_detections = int(self.interpreter.get_tensor(self.output_details[3]['index'])[0])

        detections = []
        for i in range(num_detections):
            confidence = scores[i]
            if confidence < self.confidence_threshold:
                continue
            class_id = int(classes[i])
            if class_id >= len(self.labels):
                continue
            class_name = self.labels[class_id].lower()
            if target_classes and class_name not in target_classes:
                continue
            ymin, xmin, ymax, xmax = boxes[i]
            detections.append({
                'class': class_name,
                'confidence': float(confidence),
                'box': [float(xmin), float(ymin), float(xmax), float(ymax)]
            })
        return detections

    def detect(self, image, target_classes=None, ignore_classes=None):
        """
        Run detection/classification on image.
        Returns list of detections: [{'class': 'predator', 'confidence': 0.98}, ...]

        Times the full pipeline (preprocess + invoke + decode) per call. The elapsed ms is stored
        on self.last_inference_ms and folded into rolling stats (see get_speed_stats()).
        """
        t0 = time.perf_counter()
        try:
            self._last_probs = None  # Reset cache
            input_data = self._preprocess_image(image)
            self.interpreter.set_tensor(self.input_details[0]['index'], input_data)
            self.interpreter.invoke()

            if self.model_type == 'classification':
                output = self.interpreter.get_tensor(self.output_details[0]['index'])
                probabilities = output[0]
                self._last_probs = {self.labels[i]: float(probabilities[i]) for i in range(len(self.labels))}
                return self._decode_classification(output, ignore_classes)
            elif self.model_type == 'yolo':
                output = self.interpreter.get_tensor(self.output_details[0]['index'])
                return self._decode_yolo(output, target_classes)
            elif self.model_type == 'picodet':
                boxes = self.interpreter.get_tensor(self.output_details[self._pd_box_idx]['index'])[0]
                scores = self.interpreter.get_tensor(self.output_details[self._pd_score_idx]['index'])[0]
                return self._decode_picodet(boxes, scores, target_classes)
            else:
                return self._decode_coco_ssd(target_classes)

        except Exception as e:
            logger.error(f"Detection failed: {e}")
            return []
        finally:
            # Record timing even if decode raised, so stats reflect real per-tile cost.
            self.last_inference_ms = (time.perf_counter() - t0) * 1000.0
            self._time_total_ms += self.last_inference_ms
            self._time_count += 1
            if self._time_max_ms < self.last_inference_ms:
                self._time_max_ms = self.last_inference_ms
            if self._log_every and self._time_count % self._log_every == 0:
                s = self.get_speed_stats()
                logger.info(
                    f"⏱️  Inference speed ({self.model_type}): avg {s['avg_ms']}ms "
                    f"({s['fps']} FPS/tile), max {s['max_ms']}ms, last {s['last_ms']}ms, n={s['count']}"
                )

    def detect_predators(self, image, predator_classes=None, ignore_classes=None):
        """Detect predators in image"""
        return self.detect(image, target_classes=predator_classes, ignore_classes=ignore_classes)
