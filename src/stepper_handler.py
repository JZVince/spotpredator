"""Stepper Handler for 28BYJ-48 motor via ULN2003 driver — rotates camera for surrounding scan"""
import logging
import time
try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None

logger = logging.getLogger(__name__)

# 28BYJ-48 half-step sequence (8 steps) for smoother, higher-torque motion
HALF_STEP_SEQUENCE = [
    [1, 0, 0, 0],
    [1, 1, 0, 0],
    [0, 1, 0, 0],
    [0, 1, 1, 0],
    [0, 0, 1, 0],
    [0, 0, 1, 1],
    [0, 0, 0, 1],
    [1, 0, 0, 1],
]

# 28BYJ-48 with internal 64:1 gearbox: ~4096 half-steps per full output revolution
STEPS_PER_REV = 4096


class StepperHandler:
    """28BYJ-48 stepper control via ULN2003 for camera rotation"""

    def __init__(self, pins=(5, 6, 13, 19), positions=6, step_delay=0.0015):
        """
        Initialize stepper.

        Args:
            pins: Tuple of 4 BCM GPIO pins wired to ULN2003 IN1-IN4
            positions: Number of equally-spaced scan positions around the circle
            step_delay: Delay between steps in seconds (controls speed/torque)
        """
        self.pins = list(pins)
        self.positions = positions
        self.step_delay = step_delay
        self.steps_per_position = STEPS_PER_REV // positions
        self.current_position = 0
        self.gpio_available = GPIO is not None

        if not self.gpio_available:
            logger.warning("RPi.GPIO not available, stepper disabled")
            return

        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for pin in self.pins:
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.LOW)
            logger.info(f"Stepper initialized on pins {self.pins}, {positions} positions")
        except Exception as e:
            logger.error(f"Failed to initialize stepper: {e}")
            self.gpio_available = False

    def _step(self, steps, direction=1):
        """Drive the motor a number of half-steps. direction: 1=CW, -1=CCW."""
        seq = HALF_STEP_SEQUENCE
        seq_len = len(seq)
        for _ in range(steps):
            for pin_idx in range(4):
                GPIO.output(self.pins[pin_idx], seq[self._seq_index][pin_idx])
            self._seq_index = (self._seq_index + direction) % seq_len
            time.sleep(self.step_delay)
        # De-energize coils after moving to prevent overheating and save power
        for pin in self.pins:
            GPIO.output(pin, GPIO.LOW)

    _seq_index = 0

    def next_position(self):
        """Rotate to the next scan position. Returns the new position index."""
        if not self.gpio_available:
            return self.current_position
        try:
            self._step(self.steps_per_position, direction=1)
            self.current_position = (self.current_position + 1) % self.positions
            logger.info(f"📐 Rotated to position {self.current_position}/{self.positions}")
            return self.current_position
        except Exception as e:
            logger.error(f"Failed to rotate stepper: {e}")
            return self.current_position

    def home(self):
        """Rotate back to position 0 by the shortest path."""
        if not self.gpio_available or self.current_position == 0:
            return
        try:
            # Rotate forward through remaining positions to return to 0
            steps_back = self.steps_per_position * (self.positions - self.current_position)
            self._step(steps_back, direction=1)
            self.current_position = 0
            logger.info("📐 Stepper returned home (position 0)")
        except Exception as e:
            logger.error(f"Failed to home stepper: {e}")

    def cleanup(self):
        """De-energize coils and release GPIO."""
        if self.gpio_available:
            try:
                for pin in self.pins:
                    GPIO.output(pin, GPIO.LOW)
                GPIO.cleanup(self.pins)
                logger.debug("Stepper GPIO cleaned up")
            except Exception as e:
                logger.error(f"Error cleaning up stepper GPIO: {e}")

    def __del__(self):
        self.cleanup()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Testing stepper — rotating through all positions...")
    if GPIO is None:
        print("❌ RPi.GPIO not available")
    else:
        stepper = StepperHandler(positions=6)
        if stepper.gpio_available:
            for i in range(6):
                stepper.next_position()
                time.sleep(1)
            print("Returning home...")
            stepper.home()
            stepper.cleanup()
            print("✅ Stepper test complete")
        else:
            print("❌ Stepper not available")
