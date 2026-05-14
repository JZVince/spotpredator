#!/usr/bin/env python3
"""
SpotPredator - Farm Animal Predator Detection System
Main field detector program
"""
import logging
import time
import yaml
import sys
import os
from pathlib import Path
from datetime import date, datetime

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent))

from camera_handler import CameraHandler
from detector import PredatorDetector
from rtc_handler import RTCHandler
from buzzer_handler import BuzzerHandler
from lora_handler import LoRaHandler
from alert_handler import AlertHandler

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    handlers=[
        logging.FileHandler('data/logs/spotpredator.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Scan confidence logger
scan_logger = logging.getLogger('scans')
scan_logger.setLevel(logging.INFO)
scan_handler = logging.FileHandler('data/logs/scan_confidence.log')
scan_handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s'))
scan_logger.addHandler(scan_handler)


def load_config(config_path='config.yaml'):
    """Load configuration from YAML file"""
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        logger.info(f"Loaded configuration from {config_path}")
        return config
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(1)


def main():
    """Main detection loop"""
    logger.info("=" * 60)
    logger.info("SpotPredator - Farm Animal Predator Detection System")
    logger.info("=" * 60)

    # Load configuration
    config = load_config()

    # Initialize components
    logger.info("Initializing components...")

    try:
        # Camera
        cam_config = config.get('camera', {})
        camera = CameraHandler(
            resolution=tuple(cam_config.get('resolution', [640, 480]))
        )

        # Detector
        det_config = config.get('detector', {})
        detector = PredatorDetector(
            model_path=det_config.get('model_path', 'models/detect.tflite'),
            labels_path=det_config.get('labels_path', 'models/labelmap.txt'),
            confidence_threshold=det_config.get('confidence_threshold', 0.65)
        )

        # RTC
        rtc_config = config.get('hardware', {}).get('rtc', {})
        rtc = RTCHandler(
            i2c_address=rtc_config.get('i2c_address', 0x68)
        )

        # Buzzer
        buzzer_config = config.get('hardware', {}).get('buzzer', {})
        buzzer = BuzzerHandler(
            gpio_pin=buzzer_config.get('gpio_pin', 27)
        )

        # LoRa
        lora_config = config.get('hardware', {}).get('lora', {})
        lora = LoRaHandler(
            port=lora_config.get('uart_port', '/dev/serial0'),
            baud_rate=lora_config.get('baud_rate', 115200),
            network_id=lora_config.get('network_id', 18),
            frequency=lora_config.get('frequency', 915)
        )

        # Alert handler
        alert_handler = AlertHandler(buzzer, lora, rtc, config)

        # Start camera
        if not camera.start():
            logger.error("Failed to start camera, exiting")
            sys.exit(1)

        # Verify RTC is working and time is valid
        rtc_time = rtc.get_time()
        if not rtc.rtc_available:
            logger.warning("⚠️  RTC not available - using system time")
        elif rtc_time.year < 2024:
            logger.warning(f"⚠️  RTC time looks wrong ({rtc_time}) - consider running set_time()")
        else:
            logger.info(f"✅ RTC time: {rtc_time}")

        logger.info("✅ All components initialized")
        logger.info("")

        # Startup beep
        if buzzer.gpio_available:
            buzzer.beep(count=2, beep_duration=0.1, pause_duration=0.1)

    except Exception as e:
        logger.error(f"Initialization failed: {e}")
        sys.exit(1)

    # Get settings
    check_interval = config.get('detection', {}).get('check_interval', 5)
    target_classes = config.get('detector', {}).get('target_classes', ['dog', 'cat', 'bird'])

    # Heartbeat tracking
    hb = {'last_minute': -1, 'predator_type': None, 'predator_time': None}

    # Image cleanup tracking
    last_cleanup_month = None
    last_weekly_cleanup = None

    # Scan image path
    scan_image_path = Path('data/scans/')
    scan_image_path.mkdir(parents=True, exist_ok=True)

    # Schedule settings
    schedule_enabled = config.get('detection', {}).get('schedule_enabled', False)
    start_hour = config.get('detection', {}).get('start_hour', 6)
    start_minute = config.get('detection', {}).get('start_minute', 0)
    end_hour = config.get('detection', {}).get('end_hour', 18)
    end_minute = config.get('detection', {}).get('end_minute', 0)

    logger.info(f"Target predators: {', '.join(target_classes)}")
    logger.info(f"Check interval: {check_interval} seconds")
    logger.info(f"Confidence threshold: {detector.confidence_threshold}")

    if schedule_enabled:
        logger.info(f"Schedule: Active from {start_hour:02d}:{start_minute:02d} to {end_hour:02d}:{end_minute:02d}")
    else:
        logger.info("Schedule: 24/7 detection (always active)")

    logger.info("")
    logger.info("Starting detection loop... (Press Ctrl+C to stop)")
    logger.info("")

    def is_within_schedule():
        """Check if current time is within active detection hours"""
        if not schedule_enabled:
            return True

        from datetime import datetime, time as dt_time

        now = rtc.get_time()
        current_time = now.time()

        start_time = dt_time(start_hour, start_minute)
        end_time = dt_time(end_hour, end_minute)

        return start_time <= current_time <= end_time

    def send_heartbeat():
        """Send heartbeat LoRa message every 30 minutes"""
        now = rtc.get_time()
        if hb['predator_time'] and (time.time() - hb['predator_time']) < 600:
            minutes_ago = int((time.time() - hb['predator_time']) / 60)
            msg = f"HEARTBEAT,{hb['predator_type']} seen {minutes_ago} min ago,{now.strftime('%H:%M')}"
        else:
            msg = f"HEARTBEAT,Field is clear,{now.strftime('%H:%M')}"
        lora.send_message(msg)
        logger.info(f"💓 Heartbeat sent: {msg} on {now.strftime('%Y-%m-%d')}")

    # Summary heartbeat tracking
    summary_sent_date = None

    def send_summary_heartbeats():
        """Read scan confidence log and send daily summary over LoRa after 9PM"""
        try:
            today = date.today().strftime("%Y-%m-%d")
            log_path = "data/logs/scan_confidence.log"

            total = 0
            bg_sum = poultry_sum = predator_sum = 0
            max_predator = 0
            max_predator_time = ""
            notable = 0  # scans where predator > 50%

            hourly_predator = {}  # hour -> list of predator confidences

            if os.path.exists(log_path):
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        if today not in line:
                            continue
                        try:
                            # Parse time
                            time_str = line.split(' ')[1][:5]  # HH:MM
                            hour = int(time_str.split(':')[0])

                            # Parse confidences: "background:66% | poultry:10% | predator:23%"
                            parts = line.split('|')
                            conf = {}
                            for p in parts:
                                p = p.strip()
                                if ':' in p and '%' in p:
                                    # handle "2026-05-04 16:15:21,580 | INFO | scans | background:66%..."
                                    seg = p.split()[-1] if ' ' in p else p
                                    if ':' in seg and '%' in seg:
                                        k, v = seg.split(':')
                                        conf[k.strip()] = int(v.replace('%', ''))

                            if not conf:
                                continue

                            total += 1
                            bg_sum += conf.get('background', 0)
                            poultry_sum += conf.get('poultry', 0)
                            pred_conf = conf.get('predator', 0)
                            predator_sum += pred_conf

                            if pred_conf > max_predator:
                                max_predator = pred_conf
                                max_predator_time = time_str

                            if pred_conf >= 50:
                                notable += 1

                            if hour not in hourly_predator:
                                hourly_predator[hour] = []
                            hourly_predator[hour].append(pred_conf)

                        except Exception:
                            continue

            if total == 0:
                lora.send_message("SUMMARY,No scan data for today")
                return

            avg_bg = bg_sum // total
            avg_poultry = poultry_sum // total
            avg_predator = predator_sum // total

            # Message 1: overall stats
            msg1 = f"SUMMARY1,Scans:{total} Avg bg:{avg_bg}% poultry:{avg_poultry}% predator:{avg_predator}%"
            lora.send_message(msg1)
            time.sleep(1)

            # Message 2: peak predator
            msg2 = f"SUMMARY2,Peak predator:{max_predator}% at {max_predator_time} | Notable(>50%):{notable}"
            lora.send_message(msg2)
            time.sleep(1)

            # Message 3: hourly breakdown (top hours by predator confidence)
            hourly_str = ' '.join(f"{h}h:{sum(v)//len(v)}%" for h, v in sorted(hourly_predator.items()))
            msg3 = f"SUMMARY3,{hourly_str}"
            # Truncate if too long for LoRa
            lora.send_message(msg3[:230])

            logger.info(f"📊 Summary heartbeats sent: {total} scans, peak predator {max_predator}%")

        except Exception as e:
            logger.error(f"Failed to send summary heartbeats: {e}")

    # Main loop
    last_schedule_status = None
    try:
        while True:
            try:
                # Check if we're within active hours
                is_active = is_within_schedule()

                # Log schedule status changes
                if is_active != last_schedule_status:
                    if is_active:
                        logger.info("⏰ Entering active detection hours - MONITORING ACTIVE")
                        if buzzer.gpio_available:
                            buzzer.beep(count=1, beep_duration=0.1)
                    else:
                        logger.info("💤 Outside active detection hours - SLEEPING")
                        if buzzer.gpio_available:
                            buzzer.beep(count=2, beep_duration=0.1, pause_duration=0.1)
                    last_schedule_status = is_active

                # If outside schedule, just sleep and check again
                if not is_active:
                    time.sleep(60)  # Check every minute if we're back in schedule
                    continue

                # Within schedule - do detection
                # Capture frame
                frame = camera.capture_frame()

                if frame is None:
                    logger.warning("Failed to capture frame, retrying...")
                    time.sleep(1)
                    continue

                # Run detection
                detections = detector.detect_predators(frame, predator_classes=target_classes)

                # Log all class probabilities and save scanned image
                try:
                    from PIL import Image as PILImage
                    now_dt = rtc.get_time()
                    probs = detector.get_all_probabilities(frame)
                    prob_str = ' | '.join(f"{k}:{int(v*100)}%" for k, v in probs.items())
                    scan_logger.info(prob_str)
                    if detections:
                        label = detections[0]['class'].capitalize()
                        conf = int(detections[0]['confidence'] * 100)
                    else:
                        top_class = max(probs, key=probs.get)
                        label = top_class.capitalize()
                        conf = int(probs[top_class] * 100)
                    img_name = f"{label}_{conf}%_{now_dt.strftime('%m-%d-%Y_%H-%M-%S')}.jpg"
                    PILImage.fromarray(frame[:, :, ::-1]).save(str(scan_image_path / img_name))
                except Exception as e:
                    logger.error(f"Failed to save scan image: {e}")

                if detections:
                    # Found predators!
                    new_alert = False
                    for detection in detections:
                        logger.info(f"🎯 Detected: {detection['class']} "
                                    f"(confidence: {detection['confidence']:.2f})")

                        # Track last predator for heartbeat
                        hb['predator_type'] = detection['class']
                        hb['predator_time'] = time.time()

                        # Send alert (returns True if not in cooldown)
                        if alert_handler.send_alert(detection, image=frame):
                            new_alert = True

                    # Send immediate heartbeat on new alert so display updates right away
                    if new_alert:
                        send_heartbeat()
                        hb['last_minute'] = rtc.get_time().minute

                # Check if heartbeat is due (every :00 or :30), only during active hours
                if is_active:
                    now_minute = rtc.get_time().minute
                    if now_minute in (0, 30) and now_minute != hb['last_minute']:
                        send_heartbeat()
                        hb['last_minute'] = now_minute

                # Send daily summary heartbeats after 9:05 PM once per day
                now_dt = rtc.get_time()
                if now_dt.hour == 21 and now_dt.minute == 5 and summary_sent_date != now_dt.date():
                    send_summary_heartbeats()
                    summary_sent_date = now_dt.date()

                # Weekly cleanup: delete all scan images every Monday at 6:00 AM
                now_dt = rtc.get_time()
                if now_dt.weekday() == 0 and now_dt.hour == 6 and now_dt.minute == 0 and last_weekly_cleanup != now_dt.date():
                    deleted = 0
                    for f in scan_image_path.glob('*.jpg'):
                        try:
                            f.unlink()
                            deleted += 1
                        except Exception as e:
                            logger.error(f"Failed to delete scan image {f}: {e}")
                    logger.info(f"🗑️  Weekly cleanup: deleted {deleted} scan images")
                    last_weekly_cleanup = now_dt.date()

                # Monthly cleanup: delete detection images older than 30 days
                # Runs on the 1st of each month
                today = rtc.get_time().date()
                if today.day == 1 and today.month != last_cleanup_month:
                    image_path = config.get('alerts', {}).get('image_path', 'data/detections/')
                    deleted = 0
                    cutoff = time.time() - 30 * 24 * 3600
                    for f in Path(image_path).glob('*.jpg'):
                        try:
                            if f.stat().st_mtime < cutoff:
                                f.unlink()
                                deleted += 1
                        except Exception as e:
                            logger.error(f"Failed to delete detection image {f}: {e}")
                    logger.info(f"🗑️  Monthly cleanup: deleted {deleted} detection images older than 30 days")
                    last_cleanup_month = today.month

                # Wait before next check
                time.sleep(check_interval)

            except KeyboardInterrupt:
                raise  # Re-raise to exit cleanly

            except Exception as e:
                logger.error(f"Error in detection loop: {e}")
                logger.info("Recovering in 5 seconds...")
                time.sleep(5)

    except KeyboardInterrupt:
        logger.info("")
        logger.info("Stopping...")

    finally:
        # Cleanup
        logger.info("Cleaning up...")
        camera.stop()
        buzzer.cleanup()
        lora.cleanup()
        logger.info("✅ Shutdown complete")


if __name__ == "__main__":
    main()
