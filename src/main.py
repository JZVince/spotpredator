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
from lora_handler import LoRaHandler
from alert_handler import AlertHandler
from stepper_handler import StepperHandler
from field_provision import FieldProvisioner

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
        # Log the RAW RTC state BEFORE sync_time() touches anything. This shows what the RTC kept on
        # its own (its true drift/accuracy) vs. what the system clock reads — so an online NTP
        # correction can be told apart from a real RTC fault. OSF=1 here means the RTC lost power.
        try:
            raw_rtc = rtc._read_time() if rtc.rtc_available else None
            osf = rtc.osf_is_set() if rtc.rtc_available else False
            online = rtc._has_internet()
            logger.info("🕐 RTC at startup (BEFORE sync): "
                        f"RTC={raw_rtc}, system={datetime.now().replace(microsecond=0)}, "
                        f"OSF={'SET (lost power!)' if osf else 'clear'}, "
                        f"network={'online' if online else 'offline'}")
            if raw_rtc and online:
                drift = (datetime.now() - raw_rtc).total_seconds()
                logger.info(f"🕐 RTC vs system drift before NTP correction: {drift:+.1f}s "
                            "(this much will be corrected by the online sync below)")
        except Exception as e:
            logger.warning(f"Could not log pre-sync RTC state: {e}")

        # Reconcile RTC <-> system clock at startup based on connectivity:
        #   online  -> trust the NTP-synced system clock, write it to the RTC (corrects the RTC)
        #   offline -> trust the RTC, set the system clock from it (correct time with no WiFi)
        # After this, the RTC is accurate and get_time() (which trusts the RTC) is reliable.
        rtc.sync_time()

        # LoRa
        lora_config = config.get('hardware', {}).get('lora', {})
        lora = LoRaHandler(
            port=lora_config.get('uart_port', '/dev/serial0'),
            baud_rate=lora_config.get('baud_rate', 115200),
            network_id=lora_config.get('network_id', 18),
            frequency=lora_config.get('frequency', 915)
        )
        # WiFi credentials sent by the station over LoRa (encrypted with the kit key). No server:
        # the loop just glances at received LoRa messages — see check_radio() below.
        provisioner = FieldProvisioner(lora)

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
        alert_handler = AlertHandler(lora, rtc, config)

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

        # Return the turret to center on startup BEFORE doing anything else. There is no position
        # sensor, so the stepper recovered its last physical offset from disk (data/.turret_position)
        # during init; home() now unwinds that offset back to C. This fixes the case where a crash /
        # reboot / service-restart (e.g. the camera-failure recovery, or the night WiFi reboot) left
        # the turret off-center — previously it would assume it was already centered and scan skewed.
        if stepper:
            stepper.home()

    except Exception as e:
        logger.error(f"Initialization failed: {e}")
        sys.exit(1)

    # Get settings
    check_interval = config.get('detection', {}).get('check_interval', 5)
    target_classes = config.get('detector', {}).get('target_classes', ['dog', 'cat', 'bird'])

    # --- Detection verification (false-positive filter, 2026-10-11) -------------------------
    # A first-scan detection is NOT alerted straight away. The turret nudges toward where the box
    # was (left tile -> rotate left, right tile -> right, middle -> both ways), takes a second look,
    # returns to its original spot, and only alerts if
    #       (first-scan conf + best second-scan conf) / 2  >=  confidence_threshold.
    # A real animal shows up again; a one-frame fluke (shadow, leaf, glare) usually doesn't.
    verify_cfg = config.get('detection', {}).get('verify', {})
    verify_enabled = verify_cfg.get('enabled', True)
    verify_shift = float(verify_cfg.get('shift_degrees', 22))      # ~one tile (66° FOV / 3)
    verify_floor = float(verify_cfg.get('detect_floor', 0.3))      # read conf this low on rescans
    verify_settle = float(verify_cfg.get('settle_seconds', 1.5))   # let the turret stop wobbling
    verify_edge = float(verify_cfg.get('edge_shift_degrees', 5))   # max outward nudge at L2/R2
    # +shift rotates the same way as positive offsets (the "L" positions). If the image's left
    # tile ever turns out to be the scene's right, set invert_direction: true.
    verify_sign = -1 if verify_cfg.get('invert_direction', False) else 1
    alert_threshold = config.get('detector', {}).get('confidence_threshold', 0.65)

    def verify_detection(cls, first_conf, tile_idx, tile_tag, ts):
        """Second look at a first-scan detection. Returns (confirmed, avg_conf, best_conf).
        Always leaves the turret exactly where it started."""
        if not (verify_enabled and stepper and stepper.gpio_available):
            return True, first_conf, None          # no turret -> old behaviour (alert on 1st scan)
        if tile_idx == 0:
            shifts = [verify_sign * verify_shift]            # box in left tile -> rotate left
        elif tile_idx == 2:
            shifts = [-verify_sign * verify_shift]           # box in right tile -> rotate right
        else:
            shifts = [verify_sign * verify_shift, -verify_sign * verify_shift]  # middle -> both
        best_conf, best_frame, best_tag, captured = 0.0, None, '', False
        old_thr = detector.confidence_threshold
        try:
            for shift in shifts:
                moved = stepper.nudge(shift, edge_allowance=verify_edge)  # outward at L2/R2: only ~5°
                try:
                    time.sleep(verify_settle)
                    tiles2 = camera.capture_frame()
                finally:
                    stepper.unnudge()                        # always go back to the scan position
                if tiles2 is None:
                    continue
                captured = True
                detector.confidence_threshold = verify_floor
                for i, frame2 in enumerate(tiles2):
                    for d in detector.detect_predators(frame2, predator_classes=[cls]):
                        if d['class'] == cls and d['confidence'] > best_conf:
                            best_conf, best_frame = d['confidence'], frame2
                            best_tag = f"{tile_tag}{moved:+.0f}{'LMR'[i]}"
                detector.confidence_threshold = old_thr
        except Exception as e:  # noqa: BLE001
            logger.error(f"Verification failed ({e}) — alerting on the first scan to be safe")
            return True, first_conf, None
        finally:
            detector.confidence_threshold = old_thr
            time.sleep(verify_settle)                        # settle back before normal scanning
        if not captured:
            logger.warning("Verification rescan got no image (camera) — alerting on the first scan")
            return True, first_conf, None
        avg = (first_conf + best_conf) / 2
        confirmed = avg >= alert_threshold
        scan_logger.info(f"verify | {cls} first={first_conf:.2f} second={best_conf:.2f} "
                         f"avg={avg:.2f} at={best_tag or tile_tag} -> {'CONFIRMED' if confirmed else 'REJECTED'}")
        logger.info(f"🔍 Verify {cls} [{tile_tag}]: first {first_conf:.2f}, second {best_conf:.2f}, "
                    f"avg {avg:.2f} → {'CONFIRMED — alerting' if confirmed else 'rejected as likely false positive'}")
        if best_frame is not None:  # keep the second-look image next to the first-scan image
            try:
                PILImage.fromarray(best_frame[:, :, ::-1]).save(
                    str(clean_detection_path / f"{ts}_{best_tag}_{cls}_{int(best_conf*100)}%_verify.jpg"),
                    quality=92)
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to save verification image: {e}")
        return confirmed, avg, best_conf

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
    # Scan-image saving is now WINDOWED, not every-cycle (2026-09-10): raw scan images are saved only
    # during a 5-minute window that opens AFTER a detection and RE-ARMS on each new detection. Outside
    # the window, no scan images are saved (cuts disk/heat/wear from thousands of daily frames). NOTE:
    # this only gates the raw data/scans/ images — detection images (clean + annotated) still save on
    # EVERY detection regardless of the window. The window starts on the NEXT scan after a detection
    # (the triggering scan's own scan-image is skipped; its detection image is saved separately).
    SCAN_SAVE_WINDOW_SECONDS = 300      # 5 min of scan-image saving after a detection
    scan_save_until = 0.0               # epoch time until which scan images are saved (0 = off)
    scan_cycle_count = 0  # Track total scan cycles for nightly summary

    # Camera-failure recovery — WINDOWED failure detection.
    # 2026-07-16 lesson: counting only CONSECUTIVE failures misses a FLAKY camera (loose CSI cable
    # that fails-fails-succeeds-fails...). Such a camera never hits N-in-a-row, so the old logic
    # never alerted/restarted — the device just limped at ~2.5min/cycle, stopped heartbeating, and
    # went silent with NO warning. Now we track the last N capture OUTCOMES and trip if too many
    # failed, whether or not perfectly consecutive.
    from collections import deque
    camera_outcomes = deque(maxlen=6)   # True=ok, False=failed; last 6 captures
    CAMERA_FAIL_TRIP = 4                 # trip if >= this many of the last 6 failed
    # Recovery policy: RESTART ONCE. Alert the station over LoRa and exit(1) so systemd restarts
    # the service once (re-inits the camera driver cleanly). If the camera STILL fails badly after
    # that restart, stop trying — send a "needs manual fix" alert and idle (no restart-loop when a
    # cable is truly dead). A marker file (survives the restart) records we used our one restart.
    # Recovery escalation uses TWO marker files (both cleared on the first healthy capture):
    #   stage 1: no markers          -> service restart (os._exit → systemd re-runs main.py)
    #   stage 2: RESTART marker set   -> full OS reboot (sudo reboot — clears kernel/driver/CSI state
    #                                    a service restart can't; closest software gets to a power-cycle)
    #   stage 3: REBOOT marker set    -> give up: alert "needs manual fix" and idle (no reboot loop)
    CAMERA_RESTART_MARKER = 'data/.camera_restarting'
    CAMERA_REBOOT_MARKER = 'data/.camera_rebooted'

    # Periodic post-rotation power sampling (motor-stall / rail-sag diagnostics). The ULN2003 is
    # powered from the Pi's 5V, so a stuck motor sags the Pi rail; we sample power after every Nth
    # rotation to catch it without spamming vcgencmd each cycle.
    rotation_count = 0
    POWER_CHECK_EVERY_ROTATIONS = 20    # verbose power log ~ every 20 rotations (general power probe)

    # Real-motor-stall detection (distinct from the periodic power probe above). A weak 28BYJ-48 with
    # small marginal under-rotation is EXPECTED and must be ignored — it draws no meaningful extra
    # current. A REAL mechanical stall pulls locked-rotor current on the Pi's 5V (ULN2003 is Pi-
    # powered) → undervolt DURING the move. We require several CONSECUTIVE undervolt-on-rotation
    # events before alerting, so a one-off coincidental dip doesn't cry wolf. We ALERT over LoRa but
    # NEVER stop the service — a stuck turret is not a reason to kill predator detection.
    stall_undervolt_streak = 0
    STALL_ALERT_STREAK = 3              # consecutive undervolt-on-rotation moves = real stall
    stall_alerted = False              # latch so we alert once per stall episode, not every cycle

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

    def get_cpu_temp():
        """Read the Pi CPU temperature as a short string like '58C', or '' if unavailable.
        Uses the sysfs thermal file (no subprocess); value is in millidegrees C."""
        try:
            with open('/sys/class/thermal/thermal_zone0/temp') as f:
                return f"{int(f.read().strip()) // 1000}C"
        except Exception:
            return ''

    def log_power_status(reason=""):
        """Capture and LOG the Pi's power/thermal state — called on camera failures (and any
        suspected under-power event) so we get evidence AT THE MOMENT OF FAILURE, before a manual
        power-cycle wipes it. This targets the 2026-08 investigation: intermittent camera hangs that
        clear only on a full unplug (not a software restart), suspected to be a marginal 5V rail from
        the solar-USB power path. Reads:
          - get_throttled : bitmask. 0x1=undervolt NOW, 0x2=freq-capped now, 0x4=throttled now;
                            0x10000=undervolt OCCURRED since boot, 0x40000=throttle occurred, etc.
                            Anything non-zero (esp. undervolt bits) = the 5V rail dipped = power fault.
          - measure_volts : core voltage
          - measure_temp  : SoC temperature (rules thermal in/out)
        Uses vcgencmd via subprocess; never raises (best-effort diagnostics).
        Returns True if UNDERVOLT-NOW (bit 0x1) is set, else False — lets the caller react (e.g. the
        post-rotation motor-stall check treats undervolt-during-a-move as a real stall signal)."""
        undervolt_now = False
        try:
            import subprocess

            def _vc(arg):
                try:
                    r = subprocess.run(["vcgencmd", arg], capture_output=True, text=True, timeout=5)
                    return r.stdout.strip()
                except Exception as e:  # noqa: BLE001
                    return f"<err:{e}>"

            throttled = _vc("get_throttled")   # e.g. "throttled=0x0"
            volts = _vc("measure_volts")        # e.g. "volt=0.8563V"
            temp = _vc("measure_temp")          # e.g. "temp=61.2'C"
            # Decode the throttled bitmask into a human note so the log is readable at a glance.
            note = ""
            try:
                val = int(throttled.split("=")[1], 16)
                undervolt_now = bool(val & 0x1)
                flags = []
                if val & 0x1:      flags.append("UNDERVOLT-NOW")
                if val & 0x4:      flags.append("THROTTLED-NOW")
                if val & 0x2:      flags.append("FREQCAP-NOW")
                if val & 0x10000:  flags.append("undervolt-since-boot")
                if val & 0x40000:  flags.append("throttled-since-boot")
                if val & 0x20000:  flags.append("freqcap-since-boot")
                note = (" ⚠️ " + ",".join(flags)) if flags else " (power OK)"
            except Exception:
                pass
            logger.warning(f"🔌 POWER STATUS{(' [' + reason + ']') if reason else ''}: "
                           f"{throttled} {volts} {temp}{note}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Could not read power status: {e}")
        return undervolt_now

    def send_heartbeat():
        """Send heartbeat LoRa message every 30 minutes — a 'field device is alive' ping that
        also reports CPU temperature. Predator timing lives entirely on the station now (it ages
        each PREDATOR alert it receives). Temp is appended INSIDE the status field (not a new
        comma field) so the station's existing 3-part HEARTBEAT parser is unaffected."""
        now = rtc.get_time()
        temp = get_cpu_temp()
        status = f"Field is clear {temp}".rstrip() if temp else "Field is clear"
        msg = f"HEARTBEAT,{status},{now.strftime('%H:%M')}"
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
                time.sleep(1)

            # Explicit end-of-summary marker so the STATION knows the full daily summary has arrived
            # and can fire the email report immediately — instead of the station guessing at a fixed
            # clock time. This makes the report event-driven: it follows whatever end_hour the user
            # sets (2026-09-18), so a user with an 8pm end gets their report right after 8pm.
            # The SUMMARY_END send's success is what we treat as "summary delivered" for the retry
            # logic at the call site — it's the last and most important message (triggers the report).
            time.sleep(1)
            ok = lora.send_message("SUMMARY_END,done")

            logger.info(f"📊 Summary heartbeats sent: {total} scans, peak {max_conf_class} {max_conf}%")
            return bool(ok)

        except Exception as e:
            logger.error(f"Failed to send summary heartbeats: {e}")
            return False

    def check_radio():
        """Hand any LoRa messages from the station to the WiFi provisioner. Non-blocking and cheap
        (one serial-buffer check), so it can run every cycle without slowing detection."""
        for m in lora.poll_messages():
            try:
                provisioner.handle(m['message'])
            except Exception as e:  # never let a bad radio message break detection
                logger.error(f"Error handling LoRa message: {e}")

    # Main loop — initialize to current schedule state to avoid spurious reinit on first iteration
    last_schedule_status = is_within_schedule()
    try:
        while True:
            try:
                check_radio()

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
                    else:
                        logger.info("💤 Outside active detection hours - SLEEPING")
                        # Return camera to center/start so it rests untwisted overnight
                        if stepper:
                            stepper.home()
                    last_schedule_status = is_active

                # If outside schedule: send the daily summary ONCE (right when detection ends), then
                # sleep until the next active period. The summary now fires as soon as we cross out of
                # the schedule — NOT at a hardcoded 21:xx window (2026-09-20). This follows whatever
                # end_hour the user sets (e.g. 8pm), so the station's event-driven report (triggered by
                # the SUMMARY_END marker at the end of send_summary_heartbeats) also follows end_hour.
                if not is_active:
                    now_dt = rtc.get_time()

                    # Send the day's summary once per day, the first time we find ourselves outside
                    # the schedule. send_summary_heartbeats() ends with the SUMMARY_END marker, which
                    # the station uses to fire the email report immediately. RETRY up to 3 times if the
                    # send fails (LoRa/radio hiccup), THEN give up and sleep — the device stays awake
                    # only for these few attempts, not indefinitely (2026-09-20). The station's late
                    # cron fallback covers the case where all 3 attempts still failed.
                    if summary_sent_date != now_dt.date():
                        for _attempt in range(1, 4):
                            if send_summary_heartbeats():
                                logger.info(f"📊 Summary delivered (attempt {_attempt}/3).")
                                break
                            logger.warning(f"📊 Summary send failed (attempt {_attempt}/3).")
                            if _attempt < 3:
                                time.sleep(10)  # brief gap between retries (LoRa needs spacing)
                        else:
                            logger.error("📊 Summary failed after 3 attempts — sleeping anyway; "
                                         "station's cron fallback will still send the report.")
                        summary_sent_date = now_dt.date()  # once/day regardless, so we don't loop

                    # Sleep precisely until the next start time (6:30 AM).
                    next_start = now_dt.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
                    if now_dt >= next_start:
                        next_start += timedelta(days=1)
                    sleep_seconds = (next_start - now_dt).total_seconds()
                    logger.info(f"💤 Sleeping until {next_start.strftime('%H:%M')} ({int(sleep_seconds/3600)}h {int((sleep_seconds%3600)/60)}m)")
                    # Nap in short steps instead of one long sleep, glancing at the radio between
                    # naps — evenings are when the user brings the device in and the station sends
                    # it WiFi details, which used to wait unheard until morning.
                    wake_at = time.monotonic() + sleep_seconds
                    while time.monotonic() < wake_at:
                        check_radio()
                        time.sleep(min(2, max(0, wake_at - time.monotonic())))
                    continue

                # Within schedule - do detection
                # Capture three 640x640 tiles
                tiles = camera.capture_frame()

                if tiles is None:
                    # Camera-failure recovery. Do NOT touch the (possibly hung) camera — picamera2
                    # stop()/close() can block on dead hardware (2026-07-14). Instead, once the
                    # recent-failure RATE is too high, ALERT THE STATION then sys.exit(1) so systemd
                    # (Restart=on-failure) does a clean full restart that re-inits the driver.
                    camera_outcomes.append(False)
                    fails = camera_outcomes.count(False)
                    logger.warning(f"Failed to capture frame (recent fails: {fails}/{len(camera_outcomes)})")
                    # Capture power/thermal state ON EVERY capture failure — this is the evidence that
                    # gets wiped by a manual power-cycle. If undervolt bits show here, the intermittent
                    # camera hangs are the marginal-5V-rail (solar-USB) problem, not the camera.
                    log_power_status(reason=f"capture failure {fails}/{len(camera_outcomes)}")

                    if fails >= CAMERA_FAIL_TRIP:
                        restarted = Path(CAMERA_RESTART_MARKER).exists()
                        rebooted = Path(CAMERA_REBOOT_MARKER).exists()
                        now_hm = rtc.get_time().strftime('%H:%M')

                        if not restarted:
                            # STAGE 1 — service restart. Alert, drop the RESTART marker, exit(1) so
                            # systemd re-runs main.py (re-inits the camera driver in-process).
                            logger.error(f"Camera failing ({fails}/{len(camera_outcomes)} recent) — "
                                         f"STAGE 1: service restart for a clean recovery.")
                            try:
                                lora.send_message(f"ERROR,Camera failure - restarting service,{now_hm}")
                                logger.info("📡 Sent camera-failure (service restart) alert to station.")
                            except Exception as e:
                                logger.error(f"Failed to send camera-failure alert: {e}")
                            try:
                                Path(CAMERA_RESTART_MARKER).touch()  # remember stage 1 was used
                            except Exception as e:
                                logger.error(f"Could not write restart marker: {e}")
                            # Exit with os._exit(), NOT sys.exit(): sys.exit() raises SystemExit which
                            # runs the finally: cleanup block — and that calls camera.stop(), which
                            # BLOCKS FOREVER on the hung camera (the 2026-08-18 failure). os._exit()
                            # ends the process immediately, skipping cleanup, so systemd
                            # (Restart=on-failure) does the clean service restart.
                            logger.error("Exiting now (os._exit) for systemd service restart — "
                                         "skipping cleanup so the hung camera's stop() can't block it.")
                            os._exit(1)
                        elif not rebooted:
                            # STAGE 2 — full OS reboot. A service restart didn't fix it (this is the
                            # known 2026-08 pattern: only a power-cycle helps). A full reboot re-inits
                            # the ENTIRE kernel/driver/CSI/GPIO stack — the closest software gets to a
                            # power-cycle. Drop the REBOOT marker FIRST (survives the reboot) so after
                            # the reboot we escalate to stage 3 instead of rebooting again = NO LOOP.
                            logger.error("Camera STILL failing after service restart — "
                                         "STAGE 2: full OS reboot (sudo reboot).")
                            log_power_status(reason="pre-reboot (stage 2)")
                            try:
                                lora.send_message(f"ERROR,Camera failure - rebooting device,{now_hm}")
                                logger.info("📡 Sent camera-failure (reboot) alert to station.")
                            except Exception as e:
                                logger.error(f"Failed to send reboot alert: {e}")
                            try:
                                Path(CAMERA_REBOOT_MARKER).touch()  # remember stage 2 was used
                            except Exception as e:
                                logger.error(f"Could not write reboot marker: {e}")
                            try:
                                import subprocess
                                subprocess.run(["sudo", "reboot"], timeout=10)
                            except Exception as e:
                                logger.error(f"Reboot command failed: {e} — falling through to idle.")
                            # Give the reboot a moment; if it somehow didn't take, don't spin.
                            time.sleep(30)
                            os._exit(1)
                        else:
                            # STAGE 3 — both a service restart AND a full reboot failed → real hardware
                            # fix needed (CSI ribbon, connector, or power). Stop escalating: alert and
                            # idle so we don't reboot-loop. Manual intervention required.
                            logger.error("Camera STILL failing after service restart AND full reboot — "
                                         "hardware fix needed (cable/connector/power). Idling.")
                            try:
                                lora.send_message(f"ERROR,Camera dead after restart+reboot - needs manual fix,{now_hm}")
                                logger.info("📡 Sent 'needs manual fix' alert to station.")
                            except Exception as e:
                                logger.error(f"Failed to send manual-fix alert: {e}")
                            # Idle in place (don't exit → systemd won't restart-loop). Re-send the
                            # alert periodically so it isn't missed, but rarely (every ~30 min).
                            while True:
                                time.sleep(1800)
                                try:
                                    lora.send_message(f"ERROR,Camera still down - needs manual fix,{rtc.get_time().strftime('%H:%M')}")
                                except Exception:
                                    pass

                    time.sleep(2)
                    continue

                # Successful capture — record the good outcome (windowed), and clear BOTH recovery
                # markers so the full escalation budget (service-restart → reboot → give up) is
                # available again for any FUTURE, independent failure.
                camera_outcomes.append(True)
                for _marker in (CAMERA_RESTART_MARKER, CAMERA_REBOOT_MARKER):
                    if Path(_marker).exists():
                        try:
                            Path(_marker).unlink()
                            logger.info(f"Camera healthy again — cleared {_marker}.")
                        except Exception:
                            pass

                now_dt = rtc.get_time()
                pos = stepper._offset_label(stepper.current_offset) if stepper else 'C'
                tile_names = [f'{pos}L', f'{pos}M', f'{pos}R']
                new_alert = False
                alerted_this_cycle = set()
                candidates = {}   # class -> (best first-scan detection, tile_idx, tile_tag, timestamp)
                scan_save_counter += 1
                scan_cycle_count += 1
                # Save raw scan images only while inside the post-detection window (see init). The
                # window is set/extended AFTER a detection below, so this scan (evaluated before any
                # detection this cycle) reflects a PRIOR detection — i.e. the triggering scan itself
                # is not saved, only the scans that follow it within the 5-min window.
                should_save_scan = (time.time() < scan_save_until)

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
                        # A detection (re-)arms the 5-min scan-image save window. Because
                        # should_save_scan for THIS cycle was already computed above (before this
                        # detection), the triggering scan isn't saved — saving begins next cycle and
                        # runs for 5 min, extending on each subsequent detection.
                        scan_save_until = time.time() + SCAN_SAVE_WINDOW_SECONDS
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

                            # Don't alert yet — remember the best first-scan hit per class; it is
                            # verified with a second look after all 3 tiles are checked (below).
                            c = detection['class']
                            if c not in candidates or detection['confidence'] > candidates[c][0]['confidence']:
                                candidates[c] = (detection, tile_idx, tile_tag, timestamp_str)

                # Verify each candidate (rotate toward it, rescan, return), then alert only if the
                # average of the two scans clears the threshold. Classes still in alert cooldown are
                # skipped — the alert would be suppressed anyway, so no point moving the turret.
                for c, (detection, t_idx, t_tag, ts) in candidates.items():
                    if alert_handler.in_cooldown(c):
                        continue
                    confirmed, avg_conf, _ = verify_detection(c, detection['confidence'], t_idx, t_tag, ts)
                    if confirmed:
                        alert = dict(detection, confidence=avg_conf)  # alert carries the 2-scan average
                        if alert_handler.send_alert(alert, image=None, position=t_tag):
                            new_alert = True
                            alerted_this_cycle.add(c)

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

                # Rotate camera to next scan position for the next cycle. Wait 1s FIRST so all of this
                # position's capture, detection, labeling and image saving is fully settled before the
                # turret moves — prevents the position label/name from getting tied to the wrong
                # position when the rotation follows immediately after the capture (2026-09-16 fix).
                if stepper:
                    time.sleep(1)
                    stepper.next_position()
                    rotation_count += 1
                    # Settle delay AFTER the rotation, before the next cycle captures. next_position()
                    # is synchronous (returns only when stepping is DONE), but the turret still has
                    # mechanical inertia and RINGS/wobbles briefly after the motor stops — worst on the
                    # big L2->R1 swing (3 positions), which builds the most momentum. Without this, the
                    # next capture fired while R1 was still settling → blurry/off-position R1 frame
                    # (2026-09-20). 1.5s lets the turret come to rest so every position, including R1
                    # after the long swing, is captured sharp.
                    time.sleep(1.5)

                    # --- Real-motor-stall detection (alert only, never stop) ---------------------
                    # Check the rail right after EVERY move (a stall's current draw is brief — a
                    # periodic sample would miss it). undervolt_now == the 5V rail is sagging RIGHT
                    # NOW, i.e. the just-completed move loaded it → consistent with a stuck motor.
                    # Small marginal under-rotation on this weak motor draws no extra current and
                    # won't trip this — exactly as intended (ignore the small stuff).
                    verbose = (rotation_count % POWER_CHECK_EVERY_ROTATIONS == 0)
                    undervolt = log_power_status(
                        reason="post-rotation sample" if verbose else "post-rotation stall-check")
                    if undervolt:
                        stall_undervolt_streak += 1
                        logger.warning(f"⚙️  Undervolt right after a rotation "
                                       f"({stall_undervolt_streak}/{STALL_ALERT_STREAK}) — possible "
                                       f"motor stall loading the 5V rail.")
                        if stall_undervolt_streak >= STALL_ALERT_STREAK and not stall_alerted:
                            # Confirmed REAL stall (sustained undervolt across several moves). Alert
                            # the user over radio. Do NOT stop the service — detection keeps running.
                            stall_alerted = True
                            hm = rtc.get_time().strftime('%H:%M')
                            logger.error("🚨 Motor appears STUCK (repeated undervolt on rotation) — "
                                         "alerting station over LoRa. Detection continues.")
                            try:
                                lora.send_message(f"ERROR,Motor stuck - check turret,{hm}")
                            except Exception as e:
                                logger.error(f"Failed to send motor-stuck alert: {e}")
                    else:
                        # A clean move resets the streak and re-arms the alert for a future stall.
                        if stall_undervolt_streak or stall_alerted:
                            logger.info("⚙️  Rotation power back to normal — motor stall cleared.")
                        stall_undervolt_streak = 0
                        stall_alerted = False

                # Inter-cycle wait. Set to 0 in config (2026-09-16, "push to the limit" test) so the
                # cadence is just real work: capture + synchronous inference on all 3 tiles + the 1s
                # post-capture settle before rotation. Kept as a config knob — raise check_interval
                # if the Pi runs too hot or you want to slow the scan back down. sleep(0) is a no-op.
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
        lora.cleanup()
        if stepper:
            stepper.cleanup()
        logger.info("✅ Shutdown complete")


if __name__ == "__main__":
    main()
