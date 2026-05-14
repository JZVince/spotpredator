#!/usr/bin/env python3
"""
SpotPredator - Home Display Station
Receives LoRa alerts and displays on OLED screen
"""
import logging
import time
import sys
import smtplib
import subprocess
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

        # Email and WiFi settings - loaded from .env file
        self.email_address = os.getenv("EMAIL_ADDRESS", "")
        self.email_password = os.getenv("EMAIL_PASSWORD", "")
        self.wifi_connection = os.getenv("WIFI_CONNECTION", "")
        self.last_email_date = None
        self.last_log_cleanup_month = None
        self.last_keepalive_time = 0
        self.summary_stats = {}  # Stores SUMMARY1/2/3 from field Pi
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

        draw.text((5, 2), "SpotPredator", fill=255)  # yellow zone: 0-15

        status = self.last_heartbeat_status or "Waiting..."
        # Word wrap if too long
        if len(status) > 18:
            draw.text((5, 18), status[:18], fill=255)
            draw.text((5, 30), status[18:], fill=255)
        else:
            draw.text((5, 18), status, fill=255)

        heartbeat_time = f"Last: {self.last_heartbeat_time}" if self.last_heartbeat_time else ""
        draw.text((5, 42), heartbeat_time, fill=255)

        current_time = datetime.now().strftime("%H:%M")
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

            # Format: PREDATOR,<type>,<time>,<confidence>,<date>
            if message.startswith("PREDATOR,"):
                parts = message.split(',')
                if len(parts) >= 4:
                    return {
                        'type': parts[1],
                        'time': parts[2],
                        'confidence': parts[3],
                        'date': parts[4] if len(parts) > 4 else ''
                    }

            # Format: SUMMARY1/2/3,<data>
            if message.startswith("SUMMARY"):
                parts = message.split(',', 1)
                if len(parts) == 2:
                    return {
                        'type': parts[0],  # SUMMARY1, SUMMARY2, or SUMMARY3
                        'data': parts[1]
                    }

        except Exception as e:
            logger.error(f"Failed to parse message: {e}")

        return None

    def check_for_message(self):
        """Check for incoming LoRa messages"""
        try:
            if self.lora.in_waiting > 0:
                data = self.lora.read(self.lora.in_waiting).decode().strip()

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
                                logger.info(f"💓 Heartbeat: {alert['status']} at {alert['time']}")
                                detection_logger.info(f"HEARTBEAT | {alert['status']} | {alert['time']}")
                                field_logger.info(f"HEARTBEAT | {alert['status']} | {alert['time']}")
                            elif alert['type'] in ('SUMMARY1', 'SUMMARY2', 'SUMMARY3'):
                                self.summary_stats[alert['type']] = alert['data']
                                logger.info(f"📊 {alert['type']} received: {alert['data']}")
                                field_logger.info(f"{alert['type']} | {alert['data']}")
                            else:
                                self.last_alert = alert
                                self.alert_time = time.time()
                                self.alert_active = True  # Start flashing
                                self.blink_state = False
                                detection_logger.info(f"PREDATOR | {alert['type']} | confidence={alert['confidence']} | {alert.get('time','')} | {alert.get('date','')}")
                                field_logger.info(f"PREDATOR | {alert['type']} | confidence={alert['confidence']} | {alert.get('time','')} | {alert.get('date','')}")
                                self.beep(count=1, duration=0.15)
                                self.send_alert_email(alert)
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

    def send_alert_email(self, alert):
        """Send immediate email on predator detection"""
        if not self.email_address or not self.email_password:
            return
        try:
            predator_type = alert['type']
            confidence = int(float(alert['confidence']) * 100)
            alert_time = alert.get('time', '')
            alert_date = alert.get('date', '')

            subject = f"🚨 SpotPredator Alert - {predator_type.upper()} detected!"
            body = f"Predator detected at your farm!\n\n"
            body += f"Type: {predator_type.upper()}\n"
            body += f"Confidence: {confidence}%\n"
            body += f"Time: {alert_time}\n"
            body += f"Date: {alert_date}\n"
            body += f"\nCheck your field immediately!"

            msg = MIMEMultipart()
            msg['From'] = self.email_address
            msg['To'] = self.email_address
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain', 'utf-8'))

            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(self.email_address, self.email_password)
                server.send_message(msg)

            logger.info(f"📧 Alert email sent: {predator_type} at {alert_time}")

        except Exception as e:
            logger.error(f"Failed to send alert email: {e}")
            if self.reconnect_wifi():
                try:
                    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                        server.login(self.email_address, self.email_password)
                        server.send_message(msg)
                    logger.info(f"📧 Alert email sent after WiFi reconnect")
                except Exception as e2:
                    logger.error(f"Alert email failed after reconnect: {e2}")

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
                    body += f"  Hourly predator avg: {self.summary_stats['SUMMARY3']}\n"
            else:
                body += "\n📊 FIELD SCAN SUMMARY: Not received yet\n"

            body += f"\nReport generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"

            # Send email
            msg = MIMEMultipart()
            msg['From'] = self.email_address
            msg['To'] = self.email_address
            msg['Subject'] = f"SpotPredator Daily Report - {today} - {len(predator_alerts)} alerts"
            msg.attach(MIMEText(body, 'plain', 'utf-8'))

            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(self.email_address, self.email_password)
                server.send_message(msg)

            logger.info(f"📧 Daily email sent: {len(predator_alerts)} alerts, {len(heartbeats)} heartbeats")
            self.last_email_date = date.today()
            self.summary_stats = {}  # Reset for next day

        except Exception as e:
            logger.error(f"Failed to send daily email: {e}")
            if self.reconnect_wifi():
                try:
                    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
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
                # Check for messages
                self.check_for_message()

                # Handle display
                if self.alert_active and self.last_alert:
                    # Keep flashing until heartbeat arrives
                    self.show_alert(
                        self.last_alert['type'],
                        self.last_alert['confidence'],
                        self.last_alert['time'],
                        self.last_alert.get('date', ''),
                        fill_bg=self.blink_state
                    )
                    self.blink_state = not self.blink_state
                else:
                    self.show_waiting()

                # Send daily email at 9:15PM
                now = datetime.now()
                if now.hour == 21 and now.minute == 15 and self.last_email_date != date.today():
                    self.send_daily_email()

                # WiFi keepalive ping every 10 minutes
                if time.time() - self.last_keepalive_time >= 600:
                    subprocess.run(['ping', '-c', '1', '-W', '3', '8.8.8.8'], capture_output=True)
                    self.last_keepalive_time = time.time()

                # Monthly log cleanup: 1st of month at midnight
                today = date.today()
                if today.day == 1 and now.hour == 0 and now.minute == 0 and self.last_log_cleanup_month != today.month:
                    for log_file in ['data/logs/display_station.log', 'data/logs/display_detections.log']:
                        if os.path.exists(log_file):
                            with open(log_file, 'w'):
                                pass
                    logger.info("🗑️  Monthly log cleanup: logs cleared")
                    self.last_log_cleanup_month = today.month

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
