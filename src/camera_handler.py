"""Camera Handler for Arducam Camera Module 3"""
import logging
import threading
import time
from picamera2 import Picamera2
from PIL import Image

logger = logging.getLogger(__name__)


class CameraHandler:
    """Simple camera interface for Arducam Camera Module 3"""

    def __init__(self, resolution=(640, 480)):
        """
        Initialize camera

        Args:
            resolution: Tuple of (width, height)
        """
        self.resolution = resolution
        self.camera = None
        logger.info(f"Initializing camera with resolution {resolution}")

    def start(self):
        """Start the camera"""
        try:
            self.camera = Picamera2()

            config = self.camera.create_still_configuration(
                main={"size": self.resolution, "format": "RGB888"},
                controls={
                    "AwbEnable": True,
                    # AwbMode: Daylight (1). TEST 2026-08-21 (isolated) for the ORANGE/warm cast on
                    # sun-facing positions. Full-auto AWB drifts warm when dry orange-brown grass +
                    # pale bright sky fill the frame (little cool reference), tinting the whole image
                    # orange; positions with green foliage/blue sky (e.g. R1) looked natural. Locking
                    # AWB to Daylight gives CONSISTENT outdoor color across all positions regardless
                    # of scene content. AwbEnable stays True (AwbMode selects the preset when AWB is
                    # on). Values: 0=Auto,1=Daylight,2=Cloudy,3=Tungsten,4=Fluorescent,5=Indoor.
                    # Revert (drop this line → back to full auto) if daylight-lock looks worse in
                    # overcast/low light.
                    "AwbMode": 1,
                    "NoiseReductionMode": 2,
                    "Sharpness": 2.0,
                    # AeExposureMode: 1 (Short) — restored 2026-08-23 to match the documented
                    # best-default config (commit 443092c, PROJECT_LOG "finalized June 2026"). Short
                    # biases toward FASTER shutter = less motion blur = SHARPER images. It had drifted
                    # to 0 (Normal, longer exposure → softer), which is why sharpness dropped vs. the
                    # best-agreed setup. Tradeoff (known/accepted): Short underexposes a bit in low
                    # light, but wins on sharpness in daylight — the documented right choice.
                    "AeExposureMode": 1,
                    # ExposureValue: +0.2 (EV bias). For DARK images — dawn/dusk and sun-facing
                    # (backlit) frames come out underexposed: the bright sky drags the auto-exposure
                    # meter down, and Short exposure (above) collects less light in low light. EV
                    # bias tells AE to expose brighter than its meter target, lifting the frame so
                    # the ground-level subject is visible. Tuning history: +1.0 (2026-08-29) was too
                    # bright in daytime → +0.5 (2026-09-02) still a bit bright → +0.2 (2026-09-06),
                    # a gentle lift that helps low light without washing out well-lit frames. Range
                    # ~-8..+8 (1.0 ≈ one stop). Raise if dark frames still need it; drop to revert.
                    # A physical lens hood is the complementary fix for sun glare (planned next case).
                    "ExposureValue": 0.2,
                    # AeMeteringMode: CentreWeighted was TESTED TWICE and REVERTED. 2026-08-21 test
                    # (isolated) confirmed it does NOT help: images came out slightly MORE blurry and
                    # it did nothing for the sun-facing quality issue. An earlier combined attempt was
                    # also reverted. CONCLUSION: leave AeMeteringMode at DEFAULT (matrix) — do NOT
                    # re-add. Note: the real sun-facing problem is an ORANGE/warm cast, which is a
                    # WHITE-BALANCE issue (AWB drifting warm on dry-grass/bright-sun scenes), NOT
                    # metering. If revisited, try AwbMode: Daylight or EV bias — not AeMeteringMode.
                    # Focus left at DEFAULT (no AfMode/LensPosition). Manual focus at LensPosition
                    # 0.05 was tested 2026-07 and did NOT improve things, so reverted. Earlier
                    # lessons: continuous AF (AfMode=2) causes lens drift; manual@0.0 was worse than
                    # default. Default focus (settles + holds) has been the best of the tests.
                }
            )
            self.camera.configure(config)
            self.camera.start()

            # Warm up — gives sensor time to settle exposure and white balance
            time.sleep(3)

            logger.info("Camera started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to start camera: {e}")
            return False

    def capture_frame(self, timeout=8.0):
        """
        Capture a single frame split into three 640x640 tiles (left, middle, right).

        A hung camera (e.g. 'Camera frontend has timed out' — the 2026-07-13 failure) makes
        capture_array() BLOCK FOREVER, which would silently freeze the whole detection loop.
        So we run the capture in a worker thread and give up after `timeout` seconds, returning
        None instead of hanging — the caller then handles recovery.

        Returns:
            list of numpy arrays: [left, middle, right] tiles, or None if capture fails/times out
        """
        result = {}

        def _grab():
            try:
                result['frame'] = self.camera.capture_array()
            except Exception as e:  # noqa: BLE001
                result['error'] = e

        t = threading.Thread(target=_grab, daemon=True)
        t.start()
        t.join(timeout)

        if t.is_alive():
            # Thread still running past the timeout → the camera is hung. Abandon the thread
            # (daemon; it dies with the process/restart) and report failure so the caller recovers.
            logger.error(f"Camera capture timed out after {timeout}s — camera appears hung.")
            return None
        if 'error' in result:
            logger.error(f"Failed to capture frame: {result['error']}")
            return None
        if 'frame' not in result:
            logger.error("Camera capture returned no frame.")
            return None

        frame = result['frame']
        # Crop top 440px (sky) — keeps 1920x640 ground-level view
        cropped = frame[440:, :, :]
        tiles = [
            cropped[:, 0:640, :],
            cropped[:, 640:1280, :],
            cropped[:, 1280:1920, :],
        ]
        return tiles

    def capture_and_save(self, filepath):
        """
        Capture frame and save to file

        Args:
            filepath: Path to save image

        Returns:
            bool: True if successful
        """
        try:
            frame = self.capture_frame()
            if frame is not None:
                img = Image.fromarray(frame)
                img.save(filepath)
                logger.debug(f"Saved image to {filepath}")
                return True
            return False

        except Exception as e:
            logger.error(f"Failed to save image: {e}")
            return False

    def stop(self):
        """Stop the camera"""
        try:
            if self.camera:
                self.camera.stop()
                self.camera.close()
                self.camera = None
                logger.info("Camera stopped")
        except Exception as e:
            logger.error(f"Error stopping camera: {e}")

    def restart(self):
        """Fully tear down and re-initialize the camera. Used to recover from a hung/timed-out
        sensor (e.g. 'Camera frontend has timed out' — usually a flaky CSI ribbon). Returns True
        if the camera came back and can capture, False otherwise."""
        logger.warning("🔄 Restarting camera to recover from a capture failure...")
        try:
            self.stop()
        except Exception as e:
            logger.error(f"Camera restart: stop failed (continuing to re-init anyway): {e}")
        time.sleep(2)  # let the sensor/driver settle before re-opening
        if not self.start():
            logger.error("Camera restart: start() failed — camera did not come back.")
            return False
        # Confirm it can actually produce a frame, not just initialize.
        if self.capture_frame() is None:
            logger.error("Camera restart: re-initialized but still cannot capture a frame.")
            return False
        logger.info("✅ Camera recovered after restart.")
        return True

    def __del__(self):
        """Cleanup when object is destroyed"""
        self.stop()


if __name__ == "__main__":
    # Simple test
    logging.basicConfig(level=logging.INFO)

    print("Testing camera...")
    cam = CameraHandler()

    if cam.start():
        print("✅ Camera started")

        frame = cam.capture_frame()
        if frame is not None:
            print(f"✅ Captured frame: {frame.shape}")

            # Save test image
            if cam.capture_and_save("test_capture.jpg"):
                print("✅ Saved test image to test_capture.jpg")

        cam.stop()
    else:
        print("❌ Failed to start camera")
