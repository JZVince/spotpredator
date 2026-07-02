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

# 28BYJ-48 with internal 64:1 gearbox: ~4096 half-steps per full output revolution.
# Some 12V variants differ slightly — override via steps_per_rev if rotation is off.
DEFAULT_STEPS_PER_REV = 4096


class StepperHandler:
    """28BYJ-48 stepper control via ULN2003 for camera rotation.

    Center-out scan pattern: starts at center, sweeps right to the edge, snaps
    back to center, sweeps left to the edge, snaps back to center — then repeats.
    The camera never rotates more than `steps_each_side` positions from center and
    always returns to center, so a wide/stiff camera cable never accumulates twist.

    Example with positions=6 (3 each side), degrees_per_step=66:
        center → R1 → R2 → R3 → center → L1 → L2 → L3 → center → ...
    """

    def __init__(self, pins=(5, 6, 13, 19), positions=6, step_delay=0.0015,
                 steps_per_rev=DEFAULT_STEPS_PER_REV, degrees_per_step=66):
        """
        Initialize stepper.

        Args:
            pins: Tuple of 4 BCM GPIO pins wired to ULN2003 IN1-IN4
            positions: Total view positions split evenly each side of center
            step_delay: Delay between steps in seconds (controls speed/torque)
            steps_per_rev: Half-steps for one full 360 deg output revolution (calibrate per motor)
            degrees_per_step: Angular gap between adjacent positions (~camera horizontal FOV)
        """
        self.pins = list(pins)
        self.positions = positions
        self.step_delay = step_delay
        self.steps_per_rev = steps_per_rev
        self.degrees_per_step = degrees_per_step
        # Motor half-steps to move one position (one FOV gap)
        self.steps_per_position = int((degrees_per_step / 360.0) * steps_per_rev)
        # How many positions out from center on each side
        self.steps_each_side = positions // 2
        # Center-out visit order as signed offsets from center (0 = center)
        self._visit_order = self._build_visit_order()
        self._visit_idx = 0
        self.current_offset = 0  # signed offset from center, in positions
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
            logger.info(f"Stepper initialized on pins {self.pins}: center-out, "
                        f"±{self.steps_each_side} positions, {degrees_per_step}°/step")
        except Exception as e:
            logger.error(f"Failed to initialize stepper: {e}")
            self.gpio_available = False

    def _build_visit_order(self):
        """Build center-out offset sequence: center, right edge, center, left edge, center.

        Returns a list of signed position offsets the camera visits, e.g. for
        steps_each_side=3: [0, 1, 2, 3, 0, -1, -2, -3]

        No trailing 0: the leading 0 doubles as the single center between the
        end of the left sweep and the start of the next right sweep, so the
        camera doesn't pause on center twice when the cycle wraps.
        """
        order = [0]
        for i in range(1, self.steps_each_side + 1):  # sweep right
            order.append(i)
        order.append(0)                                # snap back to center
        for i in range(1, self.steps_each_side + 1):  # sweep left
            order.append(-i)
        return order

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
    _cleaned_up = False

    def next_position(self):
        """Move to the next position in the center-out visit order.

        Rotates by however many positions separate the current offset from the
        next target (e.g. snapping from the right edge back to center is one
        multi-position move). Returns a label like 'C', 'R2', 'L3'.
        """
        if not self.gpio_available:
            return self._offset_label(self.current_offset)
        try:
            self._visit_idx = (self._visit_idx + 1) % len(self._visit_order)
            target_offset = self._visit_order[self._visit_idx]
            delta = target_offset - self.current_offset  # signed positions to move

            if delta != 0:
                direction = 1 if delta > 0 else -1
                self._step(self.steps_per_position * abs(delta), direction=direction)
                self.current_offset = target_offset

            label = self._offset_label(self.current_offset)
            logger.info(f"📐 Rotated to {label}")
            return label
        except Exception as e:
            logger.error(f"Failed to rotate stepper: {e}")
            return self._offset_label(self.current_offset)

    def _offset_label(self, offset):
        """Human/file-friendly label for a position offset: C, R1, L2, ..."""
        if offset == 0:
            return "C"
        return f"R{offset}" if offset > 0 else f"L{-offset}"

    def home(self):
        """Return to center, unwinding by the exact offset currently held."""
        if not self.gpio_available or self.current_offset == 0:
            return
        try:
            direction = -1 if self.current_offset > 0 else 1
            self._step(self.steps_per_position * abs(self.current_offset), direction=direction)
            self.current_offset = 0
            self._visit_idx = 0
            logger.info("📐 Stepper returned to center")
        except Exception as e:
            logger.error(f"Failed to home stepper: {e}")

    def jog(self, degrees, direction=1):
        """Manually rotate the camera by a raw angle for hand-aiming/adjustment.

        Does NOT change the tracked scan offset — use this to physically re-aim
        the camera (e.g. after install, or to re-center if the coupling slipped).
        After jogging, whatever the camera points at becomes the new reference,
        so follow with a fresh start (or treat the current spot as center).

        Args:
            degrees: How far to rotate, in output degrees.
            direction: 1 = clockwise, -1 = counter-clockwise.
        """
        if not self.gpio_available:
            logger.warning("Cannot jog: GPIO not available")
            return
        steps = int((abs(degrees) / 360.0) * self.steps_per_rev)
        self._step(steps, direction=1 if direction > 0 else -1)
        logger.info(f"📐 Jogged {degrees}° {'CW' if direction > 0 else 'CCW'}")

    def cleanup(self):
        """De-energize coils and release GPIO. Safe to call more than once."""
        if self.gpio_available and not self._cleaned_up:
            self._cleaned_up = True
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
    import sys
    logging.basicConfig(level=logging.INFO)

    # Optional calibration helper: `python stepper_handler.py 360` does one full
    # revolution so you can mark the shaft and check it returns to exactly the start.
    if GPIO is None:
        print("❌ RPi.GPIO not available")
        sys.exit(1)

    # Manual jog: aim the camera by hand from the terminal.
    #   python stepper_handler.py jog <degrees> [cw|ccw]
    # e.g. `python stepper_handler.py jog 30 cw`  or  `python stepper_handler.py jog 15 ccw`
    if len(sys.argv) > 1 and sys.argv[1] == "jog":
        try:
            deg = float(sys.argv[2])
        except (IndexError, ValueError):
            print("Usage: python stepper_handler.py jog <degrees> [cw|ccw]")
            sys.exit(1)
        direction = -1 if (len(sys.argv) > 3 and sys.argv[3].lower() == "ccw") else 1
        s = StepperHandler(positions=2)
        if s.gpio_available:
            print(f"Jogging {deg}° {'CCW' if direction < 0 else 'CW'}...")
            s.jog(deg, direction=direction)
            s.cleanup()
            print("✅ Done. Camera re-aimed. (Restart the service so it treats this as center.)")
        else:
            print("❌ Stepper not available")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "360":
        print("Calibration: rotating one full 360° revolution forward, then back.")
        print("Mark the shaft with tape first. It should return to the exact mark.")
        s = StepperHandler(positions=2)
        s._step(s.steps_per_rev, direction=1)
        time.sleep(1)
        s._step(s.steps_per_rev, direction=-1)
        s.cleanup()
        print("✅ If the mark is back at start, steps_per_rev is correct.")
        sys.exit(0)

    print("Testing center-out scan pattern (C → R1 R2 R3 → C → L1 L2 L3 → C)...")
    stepper = StepperHandler(positions=6, degrees_per_step=66)
    if stepper.gpio_available:
        # Walk the full visit order once
        for _ in range(len(stepper._visit_order) - 1):
            stepper.next_position()
            time.sleep(1)
        print("Ensuring centered...")
        stepper.home()
        stepper.cleanup()
        print("✅ Stepper test complete — verify cable returned to untwisted center")
    else:
        print("❌ Stepper not available")
