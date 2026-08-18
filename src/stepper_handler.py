"""Stepper Handler for 28BYJ-48 motor via ULN2003 driver — rotates camera for surrounding scan"""
import logging
import os
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
                 steps_per_rev=DEFAULT_STEPS_PER_REV, degrees_per_step=66,
                 position_file='data/.turret_position'):
        """
        Initialize stepper.

        Args:
            pins: Tuple of 4 BCM GPIO pins wired to ULN2003 IN1-IN4
            positions: Total view positions split evenly each side of center
            step_delay: Delay between steps in seconds (controls speed/torque)
            steps_per_rev: Half-steps for one full 360 deg output revolution (calibrate per motor)
            degrees_per_step: Angular gap between adjacent positions (~camera horizontal FOV)
            position_file: File that persists current_offset across restarts. There is no position
                sensor, so a fresh process can't know where the turret physically is. We save the
                offset on every move and reload it here, so home() can correctly return to center
                after a crash/reboot/service-restart (which otherwise would assume it's at C).
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
        self._position_file = position_file
        # Recover the last known physical offset (survives restarts). Defaults to 0 if no file.
        self.current_offset = self._load_position()  # signed offset from center, in positions
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

    def _load_position(self):
        """Read the last saved offset from disk (0 if missing/unreadable). No sensor exists, so
        this is how the turret's physical position survives a restart/reboot."""
        try:
            with open(self._position_file) as f:
                offset = int(f.read().strip())
            logger.info(f"📍 Recovered turret position from file: offset={offset} "
                        f"({self._offset_label(offset)})")
            return offset
        except (FileNotFoundError, ValueError):
            return 0
        except Exception as e:
            logger.error(f"Could not read turret position file (assuming center): {e}")
            return 0

    def _save_position(self, offset):
        """Persist an offset to the file WITHOUT changing current_offset. Used to record the
        TARGET before a move starts, so a mid-move crash recovers toward the destination."""
        try:
            os.makedirs(os.path.dirname(self._position_file) or '.', exist_ok=True)
            with open(self._position_file, 'w') as f:
                f.write(str(offset))
        except Exception as e:
            logger.error(f"Could not save turret position: {e}")

    def _set_offset(self, offset):
        """Set current_offset AND persist it (both in-memory and on disk)."""
        self.current_offset = offset
        self._save_position(offset)

    def _build_visit_order(self):
        """Build the scan sequence: center, sweep right, then sweep left, then repeat.

        Returns a list of signed position offsets the camera visits, e.g. for
        steps_each_side=2:  [0, 1, 2, -1, -2]  ->  C, R1, R2, L1, L2, (repeat from C)

        Move sizes (each position = degrees_per_step apart):
          C->R1, R1->R2, L1->L2  : one position (small)
          R2->L1                 : the direct delta = 3 positions CCW, sweeping back
                                    through R1 and C on the way to L1 (the one big swing)
          L2->C  (cycle wrap)    : 2 positions back to center to restart
        next_position() rotates the exact signed delta between consecutive offsets,
        so these multi-position moves are handled automatically.
        """
        order = [0]
        for i in range(1, self.steps_each_side + 1):   # sweep right: R1..Rn
            order.append(i)
        for i in range(1, self.steps_each_side + 1):   # then left: L1..Ln
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
                # Persist the TARGET to the file BEFORE moving. There's no position sensor, so if a
                # crash/reboot/power-loss hits mid-rotation, the file must reflect where we were
                # HEADED, not where we started — otherwise on restart home() would recover the old
                # position and think we're centered while the turret is stuck mid-swing (the
                # 2026-07-14 "rebooted, turret didn't return home" bug). current_offset (in-memory)
                # still updates only after the move completes.
                self._save_position(target_offset)
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
            steps = self.steps_per_position * abs(self.current_offset)
            # Save target (0) BEFORE moving: if interrupted mid-home, the file says 0 so a re-home
            # on restart is a no-op — acceptable, since we were already on our way to center.
            self._save_position(0)
            self._step(steps, direction=direction)
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

    def set_home(self):
        """Declare that the turret is PHYSICALLY at center RIGHT NOW, and sync the software to it.

        Use this after MANUALLY re-aiming/recentering the turret (by hand or via jog). It sets the
        tracked offset to 0 AND writes 0 to the position file — so the next service startup's home()
        sees offset 0 and does NOT move the turret. Without this, a manual recenter is undone on
        restart, because home() would rotate by the stale saved offset (2026-07-16 pain point).
        """
        self.current_offset = 0
        self._visit_idx = 0
        self._save_position(0)
        logger.info("📐 Home set: current physical position is now CENTER (position file = 0).")

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

    if GPIO is None:
        print("❌ RPi.GPIO not available")
        sys.exit(1)

    # Load the REAL stepper settings from config.yaml so CLI commands use the deployed geometry
    # (e.g. 72°/5-positions), not hardcoded defaults. Previously jog/test built a stepper with the
    # default 66° — misleading in logs and wrong for the scan test.
    def _cfg():
        try:
            import yaml
            with open("config.yaml") as f:
                sc = yaml.safe_load(f).get("hardware", {}).get("stepper", {})
        except Exception:
            sc = {}
        return dict(
            pins=tuple(sc.get("pins", [5, 6, 13, 19])),
            positions=sc.get("positions", 5),
            step_delay=sc.get("step_delay", 0.0015),
            steps_per_rev=sc.get("steps_per_rev", 4096),
            degrees_per_step=sc.get("degrees_per_step", 72),
        )

    # Manual jog: aim the camera by hand from the terminal.
    #   python stepper_handler.py jog <degrees> [cw|ccw]
    # e.g. `python stepper_handler.py jog 30 cw`  or  `python stepper_handler.py jog 15 ccw`
    if len(sys.argv) > 1 and sys.argv[1] == "set-home":
        # Declare the turret is physically at center NOW and sync the position file to 0, so a
        # service restart won't move it. Use AFTER manually recentering the turret.
        #
        # IMPORTANT: the running spotpredator service has its OWN in-memory position and will
        # overwrite this file on its next rotation. So set-home is only meaningful while the
        # service is STOPPED. Warn if it's active.
        import subprocess as _sp
        try:
            active = _sp.run(["systemctl", "is-active", "spotpredator"],
                             capture_output=True, text=True).stdout.strip()
        except Exception:
            active = "unknown"
        if active == "active":
            print("⚠️  The 'spotpredator' service is RUNNING — it will overwrite this setting on its")
            print("    next rotation. Stop it first:  sudo systemctl stop spotpredator")
            print("    then re-run set-home, then start it again.")
            sys.exit(1)
        s = StepperHandler(**_cfg())   # config-driven; set_home only writes 0 (no movement)
        s.set_home()
        print("✅ Home set: position file = 0. Start the service and it will treat the current spot as center.")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "jog":
        try:
            deg = float(sys.argv[2])
        except (IndexError, ValueError):
            print("Usage: python stepper_handler.py jog <degrees> [cw|ccw]")
            sys.exit(1)
        direction = -1 if (len(sys.argv) > 3 and sys.argv[3].lower() == "ccw") else 1
        s = StepperHandler(**_cfg())
        if s.gpio_available:
            print(f"Jogging {deg}° {'CCW' if direction < 0 else 'CW'}...")
            s.jog(deg, direction=direction)
            s.cleanup()
            print("✅ Done. Camera re-aimed. (Run 'set-home' while the service is stopped to treat this as center.)")
        else:
            print("❌ Stepper not available")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "360":
        print("Calibration: rotating one full 360° revolution forward, then back.")
        print("Mark the shaft with tape first. It should return to the exact mark.")
        s = StepperHandler(**_cfg())
        s._step(s.steps_per_rev, direction=1)
        time.sleep(1)
        s._step(s.steps_per_rev, direction=-1)
        s.cleanup()
        print("✅ If the mark is back at start, steps_per_rev is correct.")
        sys.exit(0)

    # SAFETY: only run the (motor-moving) scan-pattern test when EXPLICITLY asked with "test".
    # Previously ANY unrecognized argument fell through here and rotated the turret 2 cycles — a
    # footgun (a mis-typed command moved the turret). Now unknown args just print usage and exit
    # WITHOUT constructing the stepper or touching the motor.
    if not (len(sys.argv) > 1 and sys.argv[1] == "test"):
        print("Usage: python stepper_handler.py [set-home | jog <deg> [cw|ccw] | 360 | test]")
        print("  set-home : declare current position = center (writes position file, NO movement)")
        print("  jog      : rotate by a raw angle to re-aim")
        print("  360      : one full revolution (calibration)")
        print("  test     : walk the scan pattern 2 cycles (MOVES the turret)")
        sys.exit(0)

    # Scan-pattern test — uses the real deployed geometry from config.yaml.
    stepper = StepperHandler(**_cfg())

    _labels = ['C' if o == 0 else (f'R{o}' if o > 0 else f'L{-o}') for o in stepper._visit_order]
    print(f"Testing scan pattern: {' → '.join(_labels)} → (repeat)")
    print(f"  positions={_positions}, degrees_per_step={_dps}°  — assumes turret starts at CENTER")
    if stepper.gpio_available:
        # Walk two full cycles so you can see the wrap (L2 → C → R1 ...) too.
        for _cycle in range(2):
            for _ in range(len(stepper._visit_order)):
                label = stepper.next_position()
                print(f"  → {label}")
                time.sleep(1.5)
        print("Ensuring centered...")
        stepper.home()
        stepper.cleanup()
        print("✅ Scan pattern test complete — turret returned to center.")
        stepper.cleanup()
        print("✅ Stepper test complete — verify cable returned to untwisted center")
    else:
        print("❌ Stepper not available")
