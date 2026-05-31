#!/usr/bin/env python3
"""
Field device diagnostic script
Checks all components and reports status before deployment
"""
import sys
import logging
sys.path.insert(0, 'src')

logging.basicConfig(level=logging.WARNING)  # Suppress info noise during test

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

results = []

def check(name, status, detail=""):
    results.append((name, status, detail))
    symbol = "OK" if status == PASS else ("!!" if status == FAIL else "??")
    print(f"  [{symbol}] {name:<30} {detail}")

print("=" * 60)
print("SpotPredator Field Device Diagnostic")
print("=" * 60)
print()

# --- RTC ---
print("RTC:")
try:
    from rtc_handler import RTCHandler
    rtc = RTCHandler()
    t = rtc.get_time()
    if t.year >= 2024:
        check("RTC time", PASS, str(t))
    else:
        check("RTC time", FAIL, f"Wrong year: {t.year} - needs reset")
except Exception as e:
    check("RTC", FAIL, str(e))
print()

# --- Camera ---
print("Camera:")
try:
    from camera_handler import CameraHandler
    cam = CameraHandler(resolution=(1920, 1080))
    if cam.start():
        frame = cam.capture_frame()
        if frame is not None:
            check("Camera start", PASS, f"Frame shape: {frame.shape}")
            # Check frame is not all zeros (blank image)
            import numpy as np
            mean = frame.mean()
            if mean > 5:
                check("Frame content", PASS, f"Mean pixel: {mean:.1f}")
            else:
                check("Frame content", FAIL, "Frame appears blank/black")
        else:
            check("Camera capture", FAIL, "capture_frame() returned None")
        cam.stop()
    else:
        check("Camera start", FAIL, "Failed to start")
except Exception as e:
    check("Camera", FAIL, str(e))
print()

# --- LoRa ---
print("LoRa:")
try:
    import yaml
    config = yaml.safe_load(open('config.yaml'))
    lora_config = config.get('hardware', {}).get('lora', {})
    from lora_handler import LoRaHandler
    lora = LoRaHandler(
        port=lora_config.get('uart_port', '/dev/serial0'),
        baud_rate=lora_config.get('baud_rate', 115200),
        network_id=lora_config.get('network_id', 18),
        frequency=lora_config.get('frequency', 915)
    )
    if lora.lora_available:
        check("LoRa init", PASS, f"Port: {lora_config.get('uart_port')}")
        if lora.send_message("DIAG,Field device diagnostic test"):
            check("LoRa send", PASS, "Test message sent")
        else:
            check("LoRa send", FAIL, "Failed to send message")
    else:
        check("LoRa init", FAIL, "Module not responding")
    lora.cleanup()
except Exception as e:
    check("LoRa", FAIL, str(e))
print()

# --- Buzzer ---
print("Buzzer:")
try:
    buzzer_config = config.get('hardware', {}).get('buzzer', {})
    buzzer_enabled = config.get('alerts', {}).get('buzzer_enabled', True)
    from buzzer_handler import BuzzerHandler
    buzzer = BuzzerHandler(gpio_pin=buzzer_config.get('gpio_pin', 27))
    if not buzzer_enabled:
        check("Buzzer", WARN, "Disabled in config.yaml")
    elif buzzer.gpio_available:
        buzzer.beep(count=1, beep_duration=0.2)
        check("Buzzer", PASS, "Beeped once")
        buzzer.cleanup()
    else:
        check("Buzzer", FAIL, "GPIO not available")
except Exception as e:
    check("Buzzer", FAIL, str(e))
print()

# --- Model ---
print("Model:")
try:
    det_config = config.get('detector', {})
    from detector import PredatorDetector
    detector = PredatorDetector(
        model_path=det_config.get('model_path', 'models/spotpredator_classifier.tflite'),
        labels_path=det_config.get('labels_path', 'models/classifier_labels.txt'),
        confidence_threshold=det_config.get('confidence_threshold', 0.85)
    )
    if detector.interpreter:
        check("Model load", PASS, det_config.get('model_path'))
        # Run inference on a blank frame to verify model works
        import numpy as np
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        probs = detector.get_all_probabilities(blank)
        if probs:
            prob_str = ' | '.join(f"{k}:{int(v*100)}%" for k, v in probs.items())
            check("Model inference", PASS, prob_str)
            # Warn if all probs are identical (collapsed model)
            vals = list(probs.values())
            if max(vals) - min(vals) < 0.01:
                check("Model health", WARN, "All probabilities identical - possible collapse")
            else:
                check("Model health", PASS, "Probabilities vary normally")
        else:
            check("Model inference", FAIL, "No output")
    else:
        check("Model load", FAIL, "Model file not found or failed to load")
except Exception as e:
    check("Model", FAIL, str(e))
print()

# --- Config ---
print("Config:")
try:
    check("check_interval", PASS, f"{config['detection']['check_interval']}s")
    check("confidence_threshold", PASS, f"{config['detector']['confidence_threshold']}")
    schedule = config.get('detection', {})
    check("Schedule", PASS, f"{schedule.get('start_hour'):02d}:00 - {schedule.get('end_hour'):02d}:{schedule.get('end_minute', 0):02d}")
except Exception as e:
    check("Config", FAIL, str(e))
print()

# --- Summary ---
print("=" * 60)
passed = sum(1 for _, s, _ in results if s == PASS)
warned = sum(1 for _, s, _ in results if s == WARN)
failed = sum(1 for _, s, _ in results if s == FAIL)
print(f"Results: {passed} passed, {warned} warnings, {failed} failed")
if failed == 0 and warned == 0:
    print("All checks passed - device ready for deployment!")
elif failed == 0:
    print("Device ready but check warnings above.")
else:
    print("Fix failed checks before deploying.")
print("=" * 60)
