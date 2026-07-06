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
from datetime import date, datetime, timedelta
from PIL import Image as PILImage, ImageDraw

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent))

from camera_handler import CameraHandler
from detector import PredatorDetector
from rtc_handler import RTCHandler
from buzzer_handler import BuzzerHandler
from lora_handler import LoRaHandler
from alert_handler import AlertHandler
from stepper_handler import StepperHandler

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    handlers=[
        logging.FileHandler('data/logs/spotpredator.log', mode='a'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Scan confidence logger
scan_logger = logging.getLogger('scans')
scan_logger.setLevel(logging.INFO)
scan_handler = logging.FileHandler('data/logs/scan_confidence.log', mode='a')
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

        # Stepper (camera rotation)
        stepper_config = config.get('hardware', {}).get('stepper', {})
        stepper = None
        if stepper_config.get('enabled', False):
            stepper = StepperHandler(
                pins=tuple(stepper_config.get('pins', [5, 6, 13, 19])),
                positions=stepper_config.get('positions', 6),
                step_delay=stepper_config.get('step_delay', 0.0015),
                steps_per_rev=stepper_config.get('steps_per_rev', 4096),
                degrees_per_step=stepper_config.get('degrees_per_step', 66)
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
    hb = {'last_minute': -1}  # tracks which :00/:30 slot last fired a heartbeat

    # Image cleanup tracking
    last_cleanup_month = None
    last_weekly_cleanup = None

    # Scan image path
    scan_image_path = Path('data/scans/')
    scan_image_path.mkdir(parents=True, exist_ok=True)
    clean_detection_path = Path('data/detections_clean/')
    clean_detection_path.mkdir(parents=True, exist_ok=True)
    scan_save_counter = 0
    scan_save_every = 5  # Save scan images every 5th cycle
    scan_cycle_count = 0  # Track total scan cycles for nightly summary

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

    from datetime import time as dt_time
    _start_time = dt_time(start_hour, start_minute)
    _end_time = dt_time(end_hour, end_minute)

    def is_within_schedule():
        """Check if current time is within active detection hours"""
        if not schedule_enabled:
            return True
        return _start_time <= rtc.get_time().time() <= _end_time

    def send_heartbeat():
        """Send heartbeat LoRa message every 30 minutes — a pure 'field device is alive' ping.
        Predator timing lives entirely on the station now: the station ages each PREDATOR alert
        it receives ('seen N min ago') for 10 min. Heartbeats no longer carry predator info."""
        now = rtc.get_time()
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

            total = scan_cycle_count
            detection_count = 0
            max_conf = 0
            max_conf_class = ""
            max_conf_time = ""
            notable = 0  # scans where any detection > 50%

            hourly_detections = {}  # hour -> count of detections

            if os.path.exists(log_path):
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        if today not in line:
                            continue
                        try:
                            time_str = line.split(' ')[1][:5]  # HH:MM
                            hour = int(time_str.split(':')[0])

                            if 'no_detection' in line:
                                continue

                            # Count detections from all tiles
                            parts = line.split('|')
                            for p in parts:
                                p = p.strip()
                                seg = p.split()[-1] if ' ' in p else p
                                if ':' in seg and '%' in seg:
                                    k, v = seg.split(':')
                                    conf_val = int(v.replace('%', ''))
                                    detection_count += 1
                                    if conf_val >= 50:
                                        notable += 1
                                    if conf_val > max_conf:
                                        max_conf = conf_val
                                        max_conf_class = k.strip()
                                        max_conf_time = time_str
                                    if hour not in hourly_detections:
                                        hourly_detections[hour] = 0
                                    hourly_detections[hour] += 1

                        except Exception:
                            continue

            if total == 0:
                lora.send_message("SUMMARY,No scan data for today")
                return

            # Message 1: overall stats
            msg1 = f"SUMMARY1,Scans:{total} Detections:{detection_count} Notable(>50%):{notable}"
            lora.send_message(msg1)
            time.sleep(1)

            # Message 2: peak detection
            msg2 = f"SUMMARY2,Peak:{max_conf_class} {max_conf}% at {max_conf_time}"
            lora.send_message(msg2)
            time.sleep(1)

            # Message 3: hourly breakdown - split across multiple messages if needed
            hourly_parts = [f"{h}h:{v}" for h, v in sorted(hourly_detections.items())]
            chunk = []
            chunk_size = 0
            msg_index = 0
            for part in hourly_parts:
                if chunk_size + len(part) + 1 > 200:
                    lora.send_message(f"SUMMARY3_{msg_index},{' '.join(chunk)}")
                    time.sleep(1)
                    chunk = []
                    chunk_size = 0
                    msg_index += 1
                chunk.append(part)
                chunk_size += len(part) + 1
            if chunk:
                lora.send_message(f"SUMMARY3_{msg_index},{' '.join(chunk)}")

            logger.info(f"📊 Summary heartbeats sent: {total} scans, peak {max_conf_class} {max_conf}%")

        except Exception as e:
            logger.error(f"Failed to send summary heartbeats: {e}")

    # Main loop — initialize to current schedule state to avoid spurious reinit on first iteration
    last_schedule_status = is_within_schedule()
    try:
        while True:
            try:
                # Check if we're within active hours
                is_active = is_within_schedule()

                # Log schedule status changes
                if is_active != last_schedule_status:
                    if is_active:
                        logger.info("⏰ Entering active detection hours - MONITORING ACTIVE")
                        camera.stop()
                        camera.start()
                        logger.info("📷 Camera reinitialized after sleep")
                        # Ensure camera begins the day at center/start
                        if stepper:
                            stepper.home()
                        if buzzer.gpio_available:
                            buzzer.beep(count=1, beep_duration=0.1)
                    else:
                        logger.info("💤 Outside active detection hours - SLEEPING")
                        # Return camera to center/start so it rests untwisted overnight
                        if stepper:
                            stepper.home()
                        if buzzer.gpio_available:
                            buzzer.beep(count=2, beep_duration=0.1, pause_duration=0.1)
                    last_schedule_status = is_active

                # If outside schedule, send summary if due then sleep until next active period
                if not is_active:
                    now_dt = rtc.get_time()

                    # Send summary if within the 21:05-21:15 window
                    if now_dt.hour == 21 and 5 <= now_dt.minute <= 15 and summary_sent_date != now_dt.date():
                        send_summary_heartbeats()
                        summary_sent_date = now_dt.date()

                    # Wait in short intervals until summary window has passed, then sleep until 6AM
                    if now_dt.hour == 21 and now_dt.minute < 16:
                        time.sleep(60)  # Still in or before summary window, check every minute
                    else:
                        # Summary window passed, sleep precisely until next 6:00 AM
                        next_start = now_dt.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
                        if now_dt >= next_start:
                            next_start += timedelta(days=1)
                        sleep_seconds = (next_start - now_dt).total_seconds()
                        logger.info(f"💤 Sleeping until {next_start.strftime('%H:%M')} ({int(sleep_seconds/3600)}h {int((sleep_seconds%3600)/60)}m)")
                        time.sleep(sleep_seconds)
                    continue

                # Within schedule - do detection
                # Capture three 640x640 tiles
                tiles = camera.capture_frame()

                if tiles is None:
                    logger.warning("Failed to capture frame, retrying...")
                    time.sleep(1)
                    continue

                now_dt = rtc.get_time()
                pos = stepper._offset_label(stepper.current_offset) if stepper else 'C'
                tile_names = [f'{pos}L', f'{pos}M', f'{pos}R']
                new_alert = False
                alerted_this_cycle = set()
                scan_save_counter += 1
                scan_cycle_count += 1
                should_save_scan = (scan_save_counter % scan_save_every == 0)

                for tile_idx, frame in enumerate(tiles):
                    # Run detection on this tile
                    detections = detector.detect_predators(frame, predator_classes=target_classes)

                    # Log detections and save scanned image
                    try:
                        if detections:
                            label = detections[0]['class'].capitalize()
                            conf = int(detections[0]['confidence'] * 100)
                            det_str = ' | '.join(f"{d['class']}:{int(d['confidence']*100)}%" for d in detections)
                            scan_logger.info(f"tile={tile_names[tile_idx]} | {det_str}")
                        else:
                            label = 'Background'
                            conf = 0
                        if should_save_scan:
                            img_name = f"{now_dt.strftime('%Y-%m-%d_%H-%M-%S')}_{tile_names[tile_idx]}_{label}_{conf}%.jpg"
                            PILImage.fromarray(frame[:, :, ::-1]).save(str(scan_image_path / img_name), quality=92)
                    except Exception as e:
                        logger.error(f"Failed to save scan image: {e}")

                    if detections:
                        timestamp_str = now_dt.strftime('%Y-%m-%d_%H-%M-%S')
                        tile_tag = tile_names[tile_idx]
                        top = detections[0]
                        img_base = f"{timestamp_str}_{tile_tag}_{top['class']}_{int(top['confidence']*100)}%"

                        # Save clean image — all detections, no annotation
                        try:
                            PILImage.fromarray(frame[:, :, ::-1]).save(
                                str(clean_detection_path / f"{img_base}.jpg"), quality=92)
                        except Exception as e:
                            logger.error(f"Failed to save clean detection image: {e}")

                        # Save annotated image — all boxes and labels on one image
                        try:
                            annotated = PILImage.fromarray(frame[:, :, ::-1])
                            draw = ImageDraw.Draw(annotated)
                            for d in detections:
                                if 'box' in d:
                                    box = d['box']
                                    scale = 640 if max(box) <= 1.0 else 1
                                    x1, y1, x2, y2 = [int(v * scale) for v in box]
                                    draw.rectangle([x1, y1, x2, y2], outline='red', width=2)
                                    draw.text((x1, max(y1 - 10, 0)), f"{d['class']} {int(d['confidence']*100)}%", fill='red')
                            annotated.save(str(Path(alert_handler.image_path) / f"{img_base}.jpg"), quality=92)
                        except Exception as e:
                            logger.error(f"Failed to save annotated detection image: {e}")

                        for detection in detections:
                            logger.info(f"🎯 [{tile_tag}] Detected: {detection['class']} "
                                        f"(confidence: {detection['confidence']:.2f})")

                            # Only send alert once per predator class per scan cycle
                            if detection['class'] not in alerted_this_cycle:
                                if alert_handler.send_alert(detection, image=None):
                                    new_alert = True
                                    alerted_this_cycle.add(detection['class'])

                # NOTE: no immediate heartbeat on new detection anymore. The PREDATOR alert
                # (sent via alert_handler.send_alert above) is what notifies + updates the
                # station, which then ages it for 10 min. Heartbeats stay a pure 30-min ping.
                now_dt = rtc.get_time()

                # Check if heartbeat is due (every :00 or :30), only during active hours
                if is_active:
                    now_minute = now_dt.minute
                    if now_minute in (0, 30) and now_minute != hb['last_minute']:
                        send_heartbeat()
                        hb['last_minute'] = now_minute

                # Weekly cleanup: delete all scan images every Monday at start time
                if now_dt.weekday() == 0 and now_dt.hour == start_hour and now_dt.minute == start_minute and last_weekly_cleanup != now_dt.date():
                    deleted = 0
                    for f in scan_image_path.glob('*.jpg'):
                        try:
                            f.unlink()
                            deleted += 1
                        except Exception as e:
                            logger.error(f"Failed to delete scan image {f}: {e}")
                    logger.info(f"🗑️  Weekly cleanup: deleted {deleted} scan images")
                    for log_file in ['data/logs/spotpredator.log', 'data/logs/scan_confidence.log', 'data/logs/wifi_reconnect.log']:
                        try:
                            open(log_file, 'w').close()
                        except Exception as e:
                            logger.error(f"Failed to clear log {log_file}: {e}")
                    logger.info("🗑️  Weekly cleanup: logs cleared")
                    last_weekly_cleanup = now_dt.date()

                # Monthly cleanup: delete detection images older than 30 days
                today = now_dt.date()
                if today.day == 1 and today.month != last_cleanup_month:
                    image_path = config.get('alerts', {}).get('image_path', 'data/detections/')
                    cutoff = time.time() - 30 * 24 * 3600
                    # Clean both the annotated detections/ and the clean detections_clean/ folders
                    for folder_path, folder_label in [(image_path, 'detection'),
                                                       (clean_detection_path, 'clean detection')]:
                        deleted = 0
                        for f in Path(folder_path).glob('*.jpg'):
                            try:
                                if f.stat().st_mtime < cutoff:
                                    f.unlink()
                                    deleted += 1
                            except Exception as e:
                                logger.error(f"Failed to delete {folder_label} image {f}: {e}")
                        logger.info(f"🗑️  Monthly cleanup: deleted {deleted} {folder_label} images older than 30 days")
                    last_cleanup_month = today.month

                # Rotate camera to next scan position for the next cycle
                if stepper:
                    stepper.next_position()

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
        if stepper:
            stepper.cleanup()
        logger.info("✅ Shutdown complete")


if __name__ == "__main__":
    main()
