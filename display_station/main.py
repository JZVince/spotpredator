#!/usr/bin/env python3
"""
SpotPredator - Home Display Station
Receives LoRa alerts and displays on OLED screen
"""
import logging
import time
import sys
import threading
import smtplib
import subprocess
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date

# LoRa
try:
    import serial
except ImportError:
    serial = None
    print("ERROR: pyserial not installed")
    print("Run: pip3 install pyserial")
    sys.exit(1)

# OLED Display
try:
    import board
    import busio
    from adafruit_ssd1306 import SSD1306_I2C
except ImportError:
    print("ERROR: Adafruit libraries not installed")
    print("Run: pip3 install adafruit-circuitpython-ssd1306 adafruit-blinka")
    sys.exit(1)

from PIL import Image, ImageDraw

# Encrypted WiFi provisioning for the field device over LoRa
from field_link import FieldLink

# Buzzer (optional)
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False

# Load environment variables from .env file
import os
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ[key.strip()] = val.strip()

os.makedirs('data/logs', exist_ok=True)

# Flag file written by scripts/daily_report.pl when all its email send attempts fail; the OLED
# shows "Email Report Failure" while it exists, and the Perl script removes it on a successful send.
EMAIL_FAIL_FLAG = 'data/logs/.email_report_failed'

# Predator-alert emails that could not be sent (no internet) are queued here, one JSON alert per
# line, and re-sent automatically once the internet is back (2026-09-25: a ~4h router outage
# silently dropped 7 alert emails — the old code tried once + one WiFi reconnect, then gave up).
PENDING_ALERTS_FILE = 'data/logs/.pending_alert_emails.jsonl'
PENDING_RETRY_SECONDS = 120   # how often to check whether queued alerts can be sent
SMTP_TIMEOUT = 30             # seconds; without it a half-dead network can hang smtplib forever

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    handlers=[
        logging.FileHandler('data/logs/display_station.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Detection log
detection_logger = logging.getLogger('detections')
detection_logger.setLevel(logging.INFO)
detection_handler = logging.FileHandler('data/logs/display_detections.log')
detection_handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s'))
detection_logger.addHandler(detection_handler)

# Field messages log - all incoming LoRa messages from field Pi
field_logger = logging.getLogger('field_messages')
field_logger.setLevel(logging.INFO)
field_logger.propagate = False
field_handler = logging.FileHandler('data/logs/field_messages.log')
field_handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s'))
field_logger.addHandler(field_handler)


class DisplayStation:
    """Home display station for predator alerts"""

    def __init__(self, lora_port="/dev/serial0", lora_baud=115200, i2c_address=0x3C, buzzer_pin=27):
        """
        Initialize display station

        Args:
            lora_port: Serial port for LoRa module
            lora_baud: Baud rate for LoRa
            i2c_address: I2C address for OLED (usually 0x3C or 0x3D)
        """
        logger.info("Initializing display station...")

        # Initialize LoRa
        try:
            self.lora = serial.Serial(port=lora_port, baudrate=lora_baud, timeout=1)
            time.sleep(0.5)
            logger.info("✅ LoRa initialized")
        except Exception as e:
            logger.error(f"Failed to initialize LoRa: {e}")
            raise
        self._rx_buf = ""  # unfinished LoRa line carried to the next read
        # Sends the field device its WiFi details over LoRa (encrypted with the kit key). Uses this
        # same serial port, so there's never a second process fighting over it.
        self.field_link = FieldLink(self.lora)

        # Initialize OLED display
        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            self.display = SSD1306_I2C(128, 64, i2c, addr=i2c_address)
            self.display.fill(0)
            self.display.show()
            logger.info("✅ OLED display initialized")
        except Exception as e:
            logger.error(f"Failed to initialize display: {e}")
            raise

        # Initialize buzzer
        self.buzzer_pin = buzzer_pin
        if GPIO_AVAILABLE:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(buzzer_pin, GPIO.OUT)
            GPIO.output(buzzer_pin, GPIO.LOW)
            logger.info("✅ Buzzer initialized")
        else:
            logger.warning("RPi.GPIO not available, buzzer disabled")

        # Display state
        self.last_alert = None
        self.alert_time = None
        self.last_heartbeat_status = None
        self.last_heartbeat_time = None
        self.alert_active = False  # True when flashing, cleared by heartbeat
        self.blink_state = False   # Current blink state

        # Recent-predator tracking (station-side aging). When a heartbeat reports a predator
        # ("<type>_<conf>% seen Nmin ago"), we record the type/conf and OUR receive time, then
        # recompute "seen X min ago" live on every display refresh and drop it after 10 min.
        # This makes the age update smoothly between the field's 30-min heartbeats, and lets the
        # display self-expire instead of showing a frozen "1min ago" forever.
        self.recent_predator = None       # e.g. "coyote_74"  (type_conf)
        self.recent_predator_position = ''  # turret position when detected (C/L1/L2/R1/R2)
        self.recent_predator_time = None  # our time.time() when we learned of it
        self.PREDATOR_TTL = 600           # seconds to keep showing the predator (10 min)

        # Field-ERROR tracking. Display priority is: ERROR > predator "seen N min ago" > heartbeat.
        # An error must NOT be hidden by an in-progress predator countdown (a broken field device
        # is more urgent). We show the error for ERROR_TTL, and it overrides the predator line.
        self.recent_error = None          # error description text
        self.recent_error_time = None     # our time.time() when the error arrived
        self.ERROR_TTL = 3600             # keep showing the error for 1 hour, then let heartbeats show again

        # Email and WiFi settings - loaded from .env file
        self.email_address = os.getenv("EMAIL_ADDRESS", "")
        self.email_password = os.getenv("EMAIL_PASSWORD", "")
        self.wifi_connection = os.getenv("WIFI_CONNECTION", "")
        self.last_email_date = None
        self.last_log_cleanup_month = None
        self.last_keepalive_time = 0
        self._pending_lock = threading.Lock()   # guards PENDING_ALERTS_FILE
        self._flush_running = False
        self._last_pending_check = 0
        self.summary_stats = {}  # Stores SUMMARY1/2/3 from field Pi
        self.report_sent_date = None  # date (YYYY-MM-DD) we last fired the daily report (SUMMARY_END latch)

        # Clear a stale email-report-failure flag on startup (2026-09-20). The flag is a file, so it
        # survives reboots — a failure from a previous session would otherwise keep showing "(email
        # fail)" on the display forever, even after the underlying WiFi issue is fixed and the device
        # rebooted. A fresh boot = fresh state; a real failure will re-create the flag when it happens.
        try:
            if os.path.exists(EMAIL_FAIL_FLAG):
                os.remove(EMAIL_FAIL_FLAG)
                logger.info("Cleared stale email-report-failure flag on startup.")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not clear email-fail flag on startup: {e}")
        if self.email_address and self.email_password:
            logger.info("✅ Email configured")
        else:
            logger.warning("Email not configured - check .env file")

    def beep(self, count=1, duration=0.15, duty_cycle=30):
        """Beep buzzer count times using PWM to control volume (duty_cycle 0-100)"""
        if not GPIO_AVAILABLE:
            return
        pwm = GPIO.PWM(self.buzzer_pin, 1000)  # 1kHz frequency
        for i in range(count):
            pwm.start(duty_cycle)
            time.sleep(duration)
            pwm.stop()
            if i < count - 1:
                time.sleep(duration)

    def show_waiting(self):
        """Show status screen"""
        image = Image.new("1", (128, 64))
        draw = ImageDraw.Draw(image)

        # Line 1 (y=2): title
        draw.text((5, 2), "SpotPredator", fill=255)

        # Lines 2-4 (y=18,30,42): status split into 18-char chunks.
        # If a predator was seen within PREDATOR_TTL, show a LIVE-aged line ("coyote 74% seen
        # N min ago") recomputed here each refresh. After the TTL expires, drop it and fall back
        # to the last heartbeat status (which by then is usually "Field is clear").
        # Status line by PRIORITY (highest first): ERROR > predator "seen N min ago" > heartbeat.
        # Each higher tier overrides the lower ones so an urgent message is never hidden by a
        # lower-priority one that happens to be within its window. Expired tiers self-clear.
        now = time.time()

        # Tier 3 (base): routine heartbeat / normal status. If the daily-report email failed, append
        # a short "(email fail)" note to the heartbeat INSTEAD of replacing the whole line — so the
        # "field is alive" heartbeat STAYS VISIBLE (2026-09-20 fix). Previously "! Email Report
        # Failure" overrode everything with no timeout, hiding the 30-min heartbeats entirely, so the
        # user couldn't tell the field device was still working. The email-fail flag is informational,
        # not urgent — it must never hide the heartbeat.
        base = self.last_heartbeat_status or "Waiting..."
        if os.path.exists(EMAIL_FAIL_FLAG):
            base = f"{base} (email fail)"
        status = base

        # Tier 2: predator "seen N min ago" (overrides heartbeat; NOT cleared by routine heartbeats).
        if self.recent_predator and self.recent_predator_time:
            if (now - self.recent_predator_time) < self.PREDATOR_TTL:
                ptype, pconf = self.recent_predator.split('_')
                mins = int((now - self.recent_predator_time) / 60)
                ppos = getattr(self, 'recent_predator_position', '')
                pos_str = f" @{ppos}" if ppos else ""
                status = f"{ptype} {pconf}%{pos_str} seen {mins}min ago"
            else:
                self.recent_predator = None  # expired
                self.recent_predator_time = None

        # Tier 1 (highest): a recent field ERROR overrides the predator line — a broken field
        # device is more urgent than a predator countdown.
        if self.recent_error and self.recent_error_time:
            if (now - self.recent_error_time) < self.ERROR_TTL:
                status = f"⚠ FIELD ERROR: {self.recent_error}"
            else:
                self.recent_error = None  # expired
                self.recent_error_time = None

        # (Email-report-failure is now folded into the base heartbeat line above as "(email fail)",
        # so it no longer hides the heartbeat or the error/predator tiers.)
        words = status.split()
        lines = []
        current = ""
        for word in words:
            if len(current) + len(word) + (1 if current else 0) <= 18:
                current += (" " if current else "") + word
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)

        y_positions = [18, 30, 42]
        for i, line in enumerate(lines[:3]):
            draw.text((5, y_positions[i]), line, fill=255)

        # Line 5 (y=54): last heartbeat time + current time
        heartbeat_time = self.last_heartbeat_time or ""
        current_time = datetime.now().strftime("%H:%M")
        draw.text((5, 54), heartbeat_time, fill=255)
        draw.text((85, 54), current_time, fill=255)

        self.display.image(image)
        self.display.show()

    def show_alert(self, predator_type, confidence, alert_time, alert_date="", fill_bg=False):
        """
        Show predator alert on display, blinking for 10 seconds

        Args:
            predator_type: Type of predator
            confidence: Detection confidence
            alert_time: Time string of detection
            alert_date: Date string of detection
        """
        logger.info(f"🚨 ALERT DISPLAYED: {predator_type} at {alert_time}")

        image = Image.new("1", (128, 64))
        draw = ImageDraw.Draw(image)
        fg = 255 if not fill_bg else 0
        if fill_bg:
            draw.rectangle((0, 0, 128, 64), fill=255)
        draw.text((5, 2), "=== ALERT! ===", fill=fg)
        draw.text((5, 18), f"{predator_type.upper()}", fill=fg)
        draw.text((5, 30), f"Conf: {int(float(confidence)*100)}%", fill=fg)
        draw.text((5, 42), f"{alert_time}", fill=fg)
        draw.text((5, 54), f"{alert_date}", fill=fg)
        self.display.image(image)
        self.display.show()

    def parse_alert_message(self, message):
        """
        Parse alert message from LoRa

        Args:
            message: Message string

        Returns:
            dict: Parsed alert or None
        """
        try:
            # Format: HEARTBEAT,<status>,<time>
            if message.startswith("HEARTBEAT,"):
                parts = message.split(',', 2)
                if len(parts) >= 3:
                    return {
                        'type': 'heartbeat',
                        'status': parts[1],
                        'time': parts[2]
                    }

            # Format: ERROR,<description>,<time>  — field device reporting a fault
            if message.startswith("ERROR,"):
                parts = message.split(',', 2)
                if len(parts) >= 3:
                    return {
                        'type': 'error',
                        'description': parts[1],
                        'time': parts[2]
                    }

            # Format: PREDATOR,<type>,<time>,<position>,<confidence>,<date>
            # (position = C/L1/L2/R1/R2 — turret position when detected). Older field devices sent
            # PREDATOR,<type>,<time>,<confidence>,<date> (no position); detect that by field count so
            # a not-yet-updated field unit still parses correctly.
            if message.startswith("PREDATOR,"):
                parts = message.split(',')
                if len(parts) >= 6:
                    # New format with position at parts[3]
                    return {
                        'type': parts[1],
                        'time': parts[2],
                        'position': parts[3],
                        'confidence': parts[4],
                        'date': parts[5] if len(parts) > 5 else ''
                    }
                elif len(parts) >= 4:
                    # Legacy format (no position)
                    return {
                        'type': parts[1],
                        'time': parts[2],
                        'position': '',
                        'confidence': parts[3],
                        'date': parts[4] if len(parts) > 4 else ''
                    }

            # Format: SUMMARY1/2/3_chunk,<data>
            if message.startswith("SUMMARY"):
                parts = message.split(',', 1)
                if len(parts) == 2:
                    msg_type = parts[0]  # e.g. SUMMARY1, SUMMARY3_0, SUMMARY3_1
                    # Normalize SUMMARY3_N chunks into SUMMARY3
                    if msg_type.startswith("SUMMARY3_"):
                        msg_type = "SUMMARY3"
                    return {
                        'type': msg_type,
                        'data': parts[1]
                    }

        except Exception as e:
            logger.error(f"Failed to parse message: {e}")

        return None

    def check_for_message(self):
        """Read everything the LoRa module has sent and handle each received message.

        The old version only looked at the FIRST line of a read, so two messages arriving together
        (or an "+OK" from our own send followed by a message) lost the second one. Field WiFi
        acknowledgements ("PACK") go to the field link; everything else to the alert handling."""
        handled = False
        try:
            if self.lora.in_waiting > 0:
                self._rx_buf += self.lora.read(self.lora.in_waiting).decode(errors="replace")
            *lines, self._rx_buf = self._rx_buf.replace("\r", "").split("\n")
            if not self._rx_buf.startswith("+RCV") and not "+RCV=".startswith(self._rx_buf):
                self._rx_buf = ""  # stray response fragment, not a message
            for line in lines:
                line = line.strip()
                if not line.startswith("+RCV="):
                    continue
                try:  # +RCV=<addr>,<len>,<data>,<rssi>,<snr> — cut <data> by its length
                    _addr, _len, _rest = line[5:].split(",", 2)
                    payload = _rest[:int(_len)]
                except ValueError:
                    payload = ""
                if self.field_link.handle(payload):
                    handled = True
                    continue
                handled = self._handle_rcv(line) or handled
        except Exception as e:
            logger.error(f"Error checking message: {e}")
        return handled

    def _handle_rcv(self, data):
        """Handle one +RCV line (alerts, heartbeats, summaries, errors)."""
        try:
            # Parse message: +RCV=<address>,<length>,<message>,<RSSI>,<SNR>
            if data.startswith("+RCV="):
                parts = data[5:].split(',', 2)  # Get first 3 parts

                if len(parts) >= 3:
                    # Strip trailing RSSI and SNR (last two comma-separated values)
                    raw_message = parts[2]
                    msg_parts = raw_message.rsplit(',', 2)
                    message = msg_parts[0] if len(msg_parts) == 3 else raw_message

                    # Parse alert
                    alert = self.parse_alert_message(message)

                    if alert:
                        if alert['type'] == 'heartbeat':
                            self.last_heartbeat_status = alert['status']
                            self.last_heartbeat_time = alert['time']
                            self.alert_active = False  # Stop flashing on heartbeat
                            self.last_alert = None
                            # NOTE: heartbeats no longer carry predator timing. A heartbeat
                            # does NOT interrupt an in-progress predator countdown (see
                            # show_waiting) — only a new PREDATOR message or the 10-min
                            # expiry clears it.
                            logger.info(f"💓 Heartbeat: {alert['status']} at {alert['time']}")
                            detection_logger.info(f"HEARTBEAT | {alert['status']} | {alert['time']}")
                            field_logger.info(f"HEARTBEAT | {alert['status']} | {alert['time']}")
                        elif alert['type'] in ('SUMMARY1', 'SUMMARY2', 'SUMMARY3'):
                            if alert['type'] == 'SUMMARY3' and alert['type'] in self.summary_stats:
                                # Append chunks for SUMMARY3
                                self.summary_stats['SUMMARY3'] += ' ' + alert['data']
                            else:
                                self.summary_stats[alert['type']] = alert['data']
                            logger.info(f"📊 {alert['type']} received: {alert['data']}")
                            field_logger.info(f"{alert['type']} | {alert['data']}")
                        elif alert['type'] == 'SUMMARY_END':
                            # The field has finished sending the full daily summary. Fire the
                            # email report NOW — event-driven so it follows whatever end_hour the
                            # user set (2026-09-18), instead of a fixed cron time. Guard against
                            # duplicates (a re-sent marker) with a per-day latch. Run in a daemon
                            # thread so the (up-to-1-hour retry) report never blocks the display.
                            field_logger.info("SUMMARY_END | daily summary complete")
                            today_str = date.today().strftime("%Y-%m-%d")
                            if self.report_sent_date != today_str:
                                self.report_sent_date = today_str
                                logger.info("📊 Full summary received — triggering daily report.")
                                self._run_daily_report_async()
                            else:
                                logger.info("📊 SUMMARY_END again today — report already triggered, skipping.")
                        elif alert['type'] == 'error':
                            # Field device reported a fault (e.g. camera failure). Highest
                            # display priority — record it with its own timestamp so it
                            # OVERRIDES the predator countdown (see show_waiting). Buzz + email.
                            desc = alert['description']
                            self.recent_error = desc
                            self.recent_error_time = time.time()
                            self.last_heartbeat_time = alert['time']
                            self.alert_active = False
                            logger.error(f"⚠️ FIELD DEVICE ERROR: {desc} at {alert['time']}")
                            detection_logger.info(f"ERROR | {desc} | {alert['time']}")
                            field_logger.info(f"ERROR | {desc} | {alert['time']}")
                            self.beep(count=3, duration=0.2)
                            # Non-blocking: email in a background thread so a slow/hung SMTP
                            # send can't freeze the display loop.
                            self._email_async(self.send_error_email, desc, alert['time'])
                        elif str(alert['type']).startswith('SUMMARY') or 'confidence' not in alert:
                            # Any other SUMMARY-type message (e.g. "SUMMARY,No scan data for
                            # today") or anything without a confidence is NOT a predator alert.
                            # It used to fall through to the predator branch below, which set
                            # last_alert and then crashed on alert['confidence'] — the main loop
                            # then crashed again in show_alert, killing the service. That was the
                            # nightly 21:16 "reboot" (really 2 service restarts), 2026-09-29.
                            logger.info(f"ℹ️ Non-predator message logged: {alert}")
                            field_logger.info(f"{alert['type']} | {alert.get('data', alert)}")
                        else:
                            self.last_alert = alert
                            self.alert_time = time.time()
                            self.alert_active = True  # Start flashing
                            self.blink_state = False
                            # Record this predator + OUR receive time so show_waiting can age
                            # it live ("<type> <conf>% seen N min ago") for 10 min, then revert.
                            # A NEW detection resets the clock; heartbeats do NOT interrupt it.
                            try:
                                conf_pct = int(float(alert['confidence']) * 100)
                            except (ValueError, TypeError):
                                conf_pct = alert['confidence']
                            self.recent_predator = f"{alert['type']}_{conf_pct}"
                            self.recent_predator_position = alert.get('position', '')
                            self.recent_predator_time = time.time()
                            _pos = alert.get('position', '')
                            detection_logger.info(f"PREDATOR | {alert['type']} | pos={_pos} | confidence={alert['confidence']} | {alert.get('time','')} | {alert.get('date','')}")
                            field_logger.info(f"PREDATOR | {alert['type']} | pos={_pos} | confidence={alert['confidence']} | {alert.get('time','')} | {alert.get('date','')}")
                            self.beep(count=1, duration=0.15)
                            # Non-blocking: email in a background thread (see _email_async) so
                            # a slow SMTP send on flaky WiFi can't freeze the OLED refresh.
                            self._email_async(self.send_alert_email, alert)
                        return True

        except Exception as e:
            logger.error(f"Error checking message: {e}")

        return False

    def has_internet(self):
        """Check actual internet connectivity by pinging 8.8.8.8"""
        result = subprocess.run(['ping', '-c', '1', '-W', '3', '8.8.8.8'], capture_output=True)
        return result.returncode == 0

    def reconnect_wifi(self):
        """Attempt to reconnect WiFi if disconnected. Returns True if internet is reachable."""
        try:
            # Check actual internet first
            if self.has_internet():
                return True

            # Check if wlan0 hardware is available
            dev_result = subprocess.run(['nmcli', '-t', '-f', 'DEVICE,STATE', 'device'], capture_output=True, text=True)
            for line in dev_result.stdout.splitlines():
                if 'wlan0' in line:
                    if 'unavailable' in line or 'unmanaged' in line:
                        logger.error("WiFi hardware unavailable or down - skipping reconnect")
                        return False
                    break
            else:
                logger.error("wlan0 not found - skipping reconnect")
                return False

            # Try reconnecting up to 3 times
            for attempt in range(1, 4):
                logger.warning(f"No internet, reconnect attempt {attempt}/3...")
                subprocess.run(['nmcli', 'connection', 'up', self.wifi_connection], capture_output=True)
                time.sleep(10)
                if self.has_internet():
                    logger.info(f"Internet restored on attempt {attempt}")
                    return True

            logger.error("Reconnect failed after 3 attempts - no internet")
            return False

        except Exception as e:
            logger.error(f"WiFi reconnect error: {e}")
            return False

    def _email_async(self, fn, *args):
        """Run an email-sending method in a daemon thread so it NEVER blocks the display loop.
        SMTP (and its WiFi-reconnect fallback) can hang for seconds/minutes on flaky station WiFi;
        doing it inline froze the single-threaded loop (OLED stopped refreshing). Fire-and-forget:
        the email either goes out in the background or logs a failure — the display keeps running."""
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _run_daily_report_async(self):
        """Launch the Perl daily report (scripts/daily_report.pl) in a daemon thread. Triggered by
        the SUMMARY_END LoRa marker so the report fires right after the field's end-of-day summary
        arrives — event-driven, follows the user's end_hour. The Perl script builds + emails the
        report and retries on failure (every 10 min for up to 1 hr), which is why this MUST run in a
        background thread — otherwise its retry loop would freeze the display for up to an hour."""
        def _run():
            try:
                subprocess.run(
                    ["perl", "/home/pi/spotpredator/scripts/daily_report.pl"],
                    cwd="/home/pi/spotpredator",
                    capture_output=True, text=True, timeout=4000,  # >1hr to allow the full retry window
                )
                logger.info("📧 Daily report script finished.")
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to run daily report script: {e}")
        threading.Thread(target=_run, daemon=True).start()

    def _build_alert_msg(self, alert, delayed=False):
        """Build the predator-alert email. delayed=True marks one that sat in the offline queue."""
        predator_type = alert['type']
        confidence = int(float(alert['confidence']) * 100)
        alert_time = alert.get('time', '')
        alert_date = alert.get('date', '')
        position = alert.get('position', '')

        prefix = "(Delayed) " if delayed else ""
        subject = f"🚨 {prefix}SpotPredator Alert - {predator_type.upper()} detected!"
        body = f"Predator detected at your farm!\n\n"
        if delayed:
            body += ("NOTE: this alert was detected while the station had no internet and is being "
                     "sent late. Check the Time/Date below for when it actually happened.\n\n")
        body += f"Type: {predator_type.upper()}\n"
        body += f"Confidence: {confidence}%\n"
        if position:
            body += f"Location: {position}\n"
        body += f"Time: {alert_time}\n"
        body += f"Date: {alert_date}\n"
        # Explain the location code so it's actionable (turret position + frame third).
        if position:
            body += ("\nLocation key: turret position (C=center, L1/L2=left, R1/R2=right) "
                     "+ frame third (L=left, M=middle, R=right). "
                     "e.g. R2L = right-2 position, left side of view.\n")
        body += f"\nCheck your field immediately!"

        msg = MIMEMultipart()
        msg['From'] = self.email_address
        msg['To'] = self.email_address
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        return msg

    def _smtp_send(self, msg):
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=SMTP_TIMEOUT) as server:
            server.login(self.email_address, self.email_password)
            server.send_message(msg)

    def send_alert_email(self, alert):
        """Send immediate email on predator detection. If it can't be sent (no internet), the alert
        is QUEUED to PENDING_ALERTS_FILE and re-sent later by _flush_pending_alerts — never dropped."""
        if not self.email_address or not self.email_password:
            return
        try:
            self._smtp_send(self._build_alert_msg(alert))
            logger.info(f"📧 Alert email sent: {alert['type']} at {alert.get('time', '')}")
            return
        except Exception as e:
            logger.error(f"Failed to send alert email: {e}")
        if self.reconnect_wifi():
            try:
                self._smtp_send(self._build_alert_msg(alert))
                logger.info("📧 Alert email sent after WiFi reconnect")
                return
            except Exception as e2:
                logger.error(f"Alert email failed after reconnect: {e2}")
        self._queue_alert(alert)

    def _queue_alert(self, alert):
        """Append an unsent alert to the offline queue (one JSON object per line)."""
        try:
            with self._pending_lock:
                with open(PENDING_ALERTS_FILE, 'a') as f:
                    f.write(json.dumps(alert) + "\n")
            logger.warning(f"📥 Alert email queued for later: {alert['type']} at {alert.get('time', '')}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Could not queue alert email (it will be lost): {e}")

    def _build_digest_msg(self, alerts):
        """ONE email summarising every alert detected while the station was offline — so an outage
        produces a single catch-up email, not a burst of one-per-alert (user preference 2026-09-25)."""
        n = len(alerts)
        subject = f"🚨 SpotPredator - {n} predator alert{'s' if n != 1 else ''} missed while offline"
        lines = ["The station had no internet, so these alerts could not be emailed when they happened.",
                 "They were still shown on the display and logged.", ""]
        for a in alerts:
            try:
                conf = f"{int(float(a['confidence']) * 100)}%"
            except (ValueError, TypeError, KeyError):
                conf = str(a.get('confidence', '?'))
            loc = f"  Location: {a['position']}" if a.get('position') else ""
            lines.append(f"  {a.get('date', '')} {a.get('time', '')} - {a.get('type', '?').upper()} ({conf}){loc}")
        lines += ["", "Location key: turret position (C=center, L1/L2=left, R1/R2=right) "
                  "+ frame third (L=left, M=middle, R=right)."]
        msg = MIMEMultipart()
        msg['From'] = self.email_address
        msg['To'] = self.email_address
        msg['Subject'] = subject
        msg.attach(MIMEText("\n".join(lines), 'plain', 'utf-8'))
        return msg

    def _flush_pending_alerts(self):
        """When the internet is back, send ALL queued alerts as ONE digest email, then clear the queue.
        Runs in a daemon thread; only one flush at a time. If the send fails, the queue is kept."""
        try:
            if not self.has_internet():
                return
            with self._pending_lock:
                if not os.path.exists(PENDING_ALERTS_FILE):
                    return
                with open(PENDING_ALERTS_FILE) as f:
                    pending = [json.loads(l) for l in f if l.strip()]
            if not pending:
                return
            try:
                self._smtp_send(self._build_digest_msg(pending))
                logger.info(f"📧 Sent 1 catch-up email covering {len(pending)} missed alert(s)")
            except Exception as e:  # noqa: BLE001
                logger.error(f"Catch-up email failed, keeping {len(pending)} alert(s) queued: {e}")
                return
            with self._pending_lock:
                # Keep anything queued while we were sending (appended after our snapshot).
                with open(PENDING_ALERTS_FILE) as f:
                    now_lines = [json.loads(l) for l in f if l.strip()]
                newer = now_lines[len(pending):]
                if newer:
                    with open(PENDING_ALERTS_FILE, 'w') as f:
                        f.writelines(json.dumps(a) + "\n" for a in newer)
                else:
                    os.remove(PENDING_ALERTS_FILE)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error flushing queued alert emails: {e}")
        finally:
            self._flush_running = False

    def send_error_email(self, description, err_time):
        """Email the user when the field device reports a fault (e.g. camera failure)."""
        if not self.email_address or not self.email_password:
            return
        subject = "⚠️ SpotPredator - Field Device Problem"
        body = (f"The field device reported a problem:\n\n"
                f"{description}\n"
                f"Time: {err_time}\n\n"
                f"The device may have stopped detecting. Check it when you can.")
        msg = MIMEMultipart()
        msg['From'] = self.email_address
        msg['To'] = self.email_address
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=SMTP_TIMEOUT) as server:
                server.login(self.email_address, self.email_password)
                server.send_message(msg)
            logger.info(f"📧 Error email sent: {description}")
        except Exception as e:
            logger.error(f"Failed to send error email: {e}")
            if self.reconnect_wifi():
                try:
                    with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=SMTP_TIMEOUT) as server:
                        server.login(self.email_address, self.email_password)
                        server.send_message(msg)
                    logger.info("📧 Error email sent after WiFi reconnect")
                except Exception as e2:
                    logger.error(f"Error email failed after reconnect: {e2}")

    def send_daily_email(self):
        """Send daily summary email at 9PM"""
        try:
            today = date.today().strftime("%Y-%m-%d")

            # Read today's detection log
            log_path = "data/logs/display_detections.log"
            predator_alerts = []
            heartbeats = []

            if os.path.exists(log_path):
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        if today in line:
                            if "PREDATOR" in line:
                                predator_alerts.append(line.strip())
                            elif "HEARTBEAT" in line:
                                heartbeats.append(line.strip())

            # Build email body
            body = f"SpotPredator Daily Summary - {today}\n"
            body += "=" * 50 + "\n\n"

            if predator_alerts:
                body += f"🚨 PREDATOR ALERTS ({len(predator_alerts)}):\n"
                for alert in predator_alerts:
                    body += f"  {alert}\n"
            else:
                body += "✅ No predator alerts today\n"

            body += f"\n💓 HEARTBEATS RECEIVED: {len(heartbeats)}\n"
            if heartbeats:
                body += f"  First: {heartbeats[0]}\n"
                body += f"  Last:  {heartbeats[-1]}\n"

            body += f"\nLast heartbeat status: {self.last_heartbeat_status or 'Unknown'}\n"

            # Field scan summary from LoRa
            if self.summary_stats:
                body += "\n" + "=" * 50 + "\n"
                body += "📊 FIELD SCAN SUMMARY (via LoRa)\n\n"
                if 'SUMMARY1' in self.summary_stats:
                    body += f"  {self.summary_stats['SUMMARY1']}\n"
                if 'SUMMARY2' in self.summary_stats:
                    body += f"  {self.summary_stats['SUMMARY2']}\n"
                if 'SUMMARY3' in self.summary_stats:
                    body += f"  Hourly detections: {self.summary_stats['SUMMARY3']}\n"
            else:
                body += "\n📊 FIELD SCAN SUMMARY: Not received yet\n"

            body += f"\nReport generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"

            # Send email
            msg = MIMEMultipart()
            msg['From'] = self.email_address
            msg['To'] = self.email_address
            msg['Subject'] = f"SpotPredator Daily Report - {today} - {len(predator_alerts)} alerts"
            msg.attach(MIMEText(body, 'plain', 'utf-8'))

            with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=SMTP_TIMEOUT) as server:
                server.login(self.email_address, self.email_password)
                server.send_message(msg)

            logger.info(f"📧 Daily email sent: {len(predator_alerts)} alerts, {len(heartbeats)} heartbeats")
            self.last_email_date = date.today()
            self.summary_stats = {}  # Reset for next day

        except Exception as e:
            logger.error(f"Failed to send daily email: {e}")
            if self.reconnect_wifi():
                try:
                    with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=SMTP_TIMEOUT) as server:
                        server.login(self.email_address, self.email_password)
                        server.send_message(msg)
                    logger.info(f"📧 Daily email sent after WiFi reconnect")
                    self.last_email_date = date.today()
                except Exception as e2:
                    logger.error(f"Daily email failed after reconnect: {e2}")

    def run(self):
        """Main loop"""
        logger.info("Display station running...")
        logger.info("Waiting for predator alerts...")

        try:
            while True:
                # One bad iteration must NOT kill the service. Previously only Ctrl+C was caught, so any
                # error here (e.g. an OLED I2C glitch in show_waiting/show_alert -> OSError) crashed the
                # process; systemd then restarted it and the screen dropped back to "Waiting..." — which
                # looked like a reboot (2026-09-29). Now we log the full traceback and keep running.
                try:
                    # Check for messages
                    self.check_for_message()
                    # Send / resend a queued WiFi-for-the-field-device request, if any
                    self.field_link.tick()

                    # Handle display
                    # Flash the alert briefly (~10s) on a fresh detection, then fall through to
                    # show_waiting(), which renders the live "seen N min ago" countdown for 10 min.
                    if self.alert_active and self.last_alert and (time.time() - (self.alert_time or 0)) < 10:
                        self.show_alert(
                            self.last_alert['type'],
                            self.last_alert['confidence'],
                            self.last_alert['time'],
                            self.last_alert.get('date', ''),
                            fill_bg=self.blink_state
                        )
                        self.blink_state = not self.blink_state
                    else:
                        self.alert_active = False  # flashing burst done; aging takes over
                        self.show_waiting()

                    now = datetime.now()

                    # WiFi keepalive ping every 10 minutes
                    if time.time() - self.last_keepalive_time >= 600:
                        subprocess.run(['ping', '-c', '1', '-W', '3', '8.8.8.8'], capture_output=True)
                        self.last_keepalive_time = time.time()

                    # Re-send any alert emails queued during an internet outage (checked every 2 min,
                    # in a background thread so the ping/SMTP can't stall the display loop).
                    if (time.time() - self._last_pending_check >= PENDING_RETRY_SECONDS
                            and not self._flush_running and os.path.exists(PENDING_ALERTS_FILE)):
                        self._last_pending_check = time.time()
                        self._flush_running = True
                        threading.Thread(target=self._flush_pending_alerts, daemon=True).start()

                    # Monthly log cleanup: 1st of month at midnight
                    today = date.today()
                    if today.day == 1 and now.hour == 0 and now.minute == 0 and self.last_log_cleanup_month != today.month:
                        for log_file in ['data/logs/display_station.log', 'data/logs/display_detections.log', 'data/logs/wifi_reconnect.log']:
                            if os.path.exists(log_file):
                                with open(log_file, 'w'):
                                    pass
                        logger.info("🗑️  Monthly log cleanup: logs cleared")
                        self.last_log_cleanup_month = today.month

                except Exception:  # noqa: BLE001
                    logger.exception("Main loop error — continuing (see traceback)")
                    time.sleep(1)
                time.sleep(0.5)  # Check twice per second

        except KeyboardInterrupt:
            logger.info("\nStopping...")

        finally:
            # Cleanup
            self.display.fill(0)
            self.display.show()
            if GPIO_AVAILABLE:
                GPIO.cleanup()
            self.lora.close()
            logger.info("Display station stopped")


def main():
    """Main entry point"""
    logger.info("=" * 50)
    logger.info("SpotPredator - Home Display Station")
    logger.info("=" * 50)

    try:
        station = DisplayStation(
            lora_port="/dev/serial0",
            lora_baud=115200,
            i2c_address=0x3C  # Change to 0x3D if your OLED uses that address
        )

        station.run()

    except Exception as e:
        logger.error(f"Failed to start display station: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
