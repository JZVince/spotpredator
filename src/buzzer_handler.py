"""Buzzer Handler for SFM-27-W Alarm (via transistor circuit)"""
import logging
import time
try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None

logger = logging.getLogger(__name__)


class BuzzerHandler:
    """Simple buzzer control via GPIO with transistor"""

    def __init__(self, gpio_pin=27):
        """
        Initialize buzzer

        Args:
            gpio_pin: GPIO pin number (BCM numbering)
        """
        self.gpio_pin = gpio_pin
        self.gpio_available = GPIO is not None

        if not self.gpio_available:
            logger.warning("RPi.GPIO not available, buzzer disabled")
            return

        try:
            # Use BCM pin numbering
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)

            # Setup pin as output
            GPIO.setup(self.gpio_pin, GPIO.OUT)
            GPIO.output(self.gpio_pin, GPIO.LOW)  # Start with buzzer off

            logger.info(f"Buzzer initialized on GPIO{self.gpio_pin}")

        except Exception as e:
            logger.error(f"Failed to initialize buzzer: {e}")
            self.gpio_available = False

    def buzz(self, duration=3):
        """
        Activate buzzer for specified duration

        Args:
            duration: Duration in seconds
        """
        if not self.gpio_available:
            logger.warning("Buzzer not available, skipping")
            return

        try:
            logger.info(f"Activating buzzer for {duration} seconds")
            GPIO.output(self.gpio_pin, GPIO.HIGH)
            time.sleep(duration)
            GPIO.output(self.gpio_pin, GPIO.LOW)
            logger.debug("Buzzer deactivated")

        except Exception as e:
            logger.error(f"Failed to activate buzzer: {e}")
            # Make sure buzzer is off
            try:
                GPIO.output(self.gpio_pin, GPIO.LOW)
            except Exception:
                pass

    def beep(self, count=1, beep_duration=0.2, pause_duration=0.2):
        """
        Make beeping sounds

        Args:
            count: Number of beeps
            beep_duration: Duration of each beep in seconds
            pause_duration: Pause between beeps in seconds
        """
        if not self.gpio_available:
            return

        try:
            for i in range(count):
                GPIO.output(self.gpio_pin, GPIO.HIGH)
                time.sleep(beep_duration)
                GPIO.output(self.gpio_pin, GPIO.LOW)

                if i < count - 1:  # Don't pause after last beep
                    time.sleep(pause_duration)

        except Exception as e:
            logger.error(f"Failed to beep: {e}")
            try:
                GPIO.output(self.gpio_pin, GPIO.LOW)
            except Exception:
                pass

    def cleanup(self):
        """Cleanup GPIO"""
        if self.gpio_available:
            try:
                GPIO.output(self.gpio_pin, GPIO.LOW)
                GPIO.cleanup(self.gpio_pin)
                logger.debug("Buzzer GPIO cleaned up")
            except Exception as e:
                logger.error(f"Error cleaning up GPIO: {e}")

    def __del__(self):
        """Cleanup when object is destroyed"""
        self.cleanup()


if __name__ == "__main__":
    # Simple test
    logging.basicConfig(level=logging.INFO)

    print("Testing buzzer...")
    print("Note: This requires proper transistor circuit wiring")

    if GPIO is None:
        print("❌ RPi.GPIO not available")
    else:
        buzzer = BuzzerHandler(gpio_pin=27)

        if buzzer.gpio_available:
            print("Testing single beep...")
            buzzer.beep(count=1)
            time.sleep(1)

            print("Testing 3 beeps...")
            buzzer.beep(count=3)
            time.sleep(1)

            print("Testing long buzz (2 seconds)...")
            buzzer.buzz(duration=2)

            print("✅ Buzzer test complete")
            buzzer.cleanup()
        else:
            print("❌ Buzzer not available")
