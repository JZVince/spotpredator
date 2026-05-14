"""Alert Handler - Coordinates buzzer and LoRa alerts"""
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


class AlertHandler:
    """Simple alert coordinator for buzzer and LoRa"""

    def __init__(self, buzzer, lora, rtc, config):
        """
        Initialize alert handler

        Args:
            buzzer: BuzzerHandler instance
            lora: LoRaHandler instance
            rtc: RTCHandler instance
            config: Configuration dict
        """
        self.buzzer = buzzer
        self.lora = lora
        self.rtc = rtc
        self.config = config

        # Alert settings from config
        self.buzzer_enabled = config.get('alerts', {}).get('buzzer_enabled', True)
        self.lora_enabled = config.get('alerts', {}).get('lora_broadcast', True)
        self.save_images = config.get('alerts', {}).get('save_images', True)
        self.image_path = config.get('alerts', {}).get('image_path', 'data/detections/')
        self.log_path = config.get('alerts', {}).get('log_path', 'data/logs/detections.log')

        # Cooldown tracking (prevent alert spam)
        self.last_alert_time = {}
        self.cooldown_seconds = config.get('detection', {}).get('cooldown_period', 30)

        # Ensure directories exist
        os.makedirs(self.image_path, exist_ok=True)
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

        logger.info("Alert handler initialized")

    def _should_alert(self, predator_type):
        """
        Check if we should alert for this predator (cooldown logic)

        Args:
            predator_type: Type of predator

        Returns:
            bool: True if should alert
        """
        now = datetime.now()

        if predator_type in self.last_alert_time:
            last_time = self.last_alert_time[predator_type]
            if (now - last_time).total_seconds() < self.cooldown_seconds:
                logger.debug(f"Cooldown active for {predator_type}, skipping alert")
                return False

        self.last_alert_time[predator_type] = now
        return True

    def _log_detection(self, predator_type, confidence, timestamp):
        """
        Log detection to file

        Args:
            predator_type: Type of predator
            confidence: Detection confidence
            timestamp: Detection timestamp
        """
        try:
            with open(self.log_path, 'a') as f:
                f.write(f"{timestamp} | {predator_type} | confidence={confidence:.2f}\n")
        except Exception as e:
            logger.error(f"Failed to log detection: {e}")

    def send_alert(self, detection, image=None):
        """
        Send alert for detected predator

        Args:
            detection: Detection dict from detector
            image: Optional PIL Image or numpy array to save

        Returns:
            bool: True if alert sent successfully
        """
        predator_type = detection['class']
        confidence = detection['confidence']

        # Check cooldown
        if not self._should_alert(predator_type):
            return False

        # Get timestamp
        timestamp_str = self.rtc.get_timestamp_string("%Y-%m-%d %H:%M:%S")
        time_short = self.rtc.get_timestamp_string("%H:%M")
        date_short = self.rtc.get_timestamp_string("%m/%d/%Y")

        logger.info(f"🚨 PREDATOR DETECTED: {predator_type} (confidence: {confidence:.2f})")

        # Log detection
        self._log_detection(predator_type, confidence, timestamp_str)

        # Save image
        if self.save_images and image is not None:
            try:
                from PIL import Image as PILImage

                # Generate filename
                safe_timestamp = timestamp_str.replace(' ', '_').replace(':', '-')
                filename = f"{predator_type}_{safe_timestamp}_{int(confidence*100)}.jpg"
                filepath = os.path.join(self.image_path, filename)

                # Save
                if hasattr(image, 'save'):  # PIL Image
                    image.save(filepath)
                else:  # Numpy array
                    import numpy as np
                    PILImage.fromarray(image).save(filepath)

                logger.info(f"Saved detection image: {filename}")

            except Exception as e:
                logger.error(f"Failed to save image: {e}")

        # Activate buzzer
        if self.buzzer_enabled and self.buzzer.gpio_available:
            try:
                duration = self.config.get('hardware', {}).get('buzzer', {}).get('duration_seconds', 3)
                self.buzzer.buzz(duration=duration)
                logger.info("Buzzer activated")
            except Exception as e:
                logger.error(f"Failed to activate buzzer: {e}")

        # Send LoRa alert
        if self.lora_enabled and self.lora.lora_available:
            try:
                self.lora.send_alert(predator_type, confidence, time_short, date_short)
                logger.info("LoRa alert sent")
            except Exception as e:
                logger.error(f"Failed to send LoRa alert: {e}")

        return True


if __name__ == "__main__":
    # Simple test
    logging.basicConfig(level=logging.INFO)

    print("Testing alert handler...")

    # Create mock objects
    class MockBuzzer:
        gpio_available = False
        def buzz(self, duration):
            print(f"  [MOCK] Buzzer activated for {duration}s")

    class MockLoRa:
        lora_available = False
        def send_alert(self, ptype, conf, time):
            print(f"  [MOCK] LoRa: {ptype}, {conf}, {time}")

    class MockRTC:
        def get_timestamp_string(self, fmt):
            return datetime.now().strftime(fmt)

    config = {
        'alerts': {
            'buzzer_enabled': True,
            'lora_broadcast': True,
            'save_images': True,
            'image_path': 'data/detections/',
            'log_path': 'data/logs/detections.log'
        },
        'hardware': {
            'buzzer': {'duration_seconds': 3}
        },
        'detection': {
            'cooldown_period': 30
        }
    }

    buzzer = MockBuzzer()
    lora = MockLoRa()
    rtc = MockRTC()

    handler = AlertHandler(buzzer, lora, rtc, config)

    # Test detection
    detection = {
        'class': 'dog',
        'confidence': 0.87,
        'box': [0.1, 0.2, 0.3, 0.4]
    }

    print("\nSending test alert...")
    handler.send_alert(detection)

    print("\n✅ Alert handler test complete")
