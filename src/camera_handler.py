"""Camera Handler for Arducam Camera Module 3"""
import logging
from picamera2 import Picamera2
from PIL import Image
import numpy as np

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

            # Simple configuration
            config = self.camera.create_still_configuration(
                main={"size": self.resolution, "format": "RGB888"}
            )
            self.camera.configure(config)
            self.camera.start()

            # Warm up and autofocus
            import time
            time.sleep(2)
            try:
                self.camera.autofocus_cycle()
            except Exception:
                pass  # Autofocus not available on all cameras

            logger.info("Camera started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to start camera: {e}")
            return False

    def capture_frame(self):
        """
        Capture a single frame

        Returns:
            numpy array: Image as RGB array, or None if capture fails
        """
        try:
            # Capture frame as numpy array
            frame = self.camera.capture_array()
            return frame

        except Exception as e:
            logger.error(f"Failed to capture frame: {e}")
            return None

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
                logger.info("Camera stopped")
        except Exception as e:
            logger.error(f"Error stopping camera: {e}")

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
