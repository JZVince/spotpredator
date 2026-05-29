#!/usr/bin/env python3
"""Test camera functionality"""
import sys
sys.path.insert(0, 'src')

from camera_handler import CameraHandler
from PIL import Image as PILImage
import logging

logging.basicConfig(level=logging.INFO)

print("=" * 50)
print("Camera Test")
print("=" * 50)
print()

camera = CameraHandler(resolution=(1920, 1080))

if camera.start():
    print("Camera started successfully")

    # Capture test image
    print("\nCapturing test image...")
    frame = camera.capture_frame()

    if frame is not None:
        print(f"Frame shape: {frame.shape}, dtype: {frame.dtype}")

        # Save with BGR fix
        PILImage.fromarray(frame[:, :, ::-1]).save("test_camera.jpg", quality=92)
        print("Saved as test_camera.jpg (BGR corrected)")
    else:
        print("Failed to capture frame")

    camera.stop()
else:
    print("Failed to start camera")
    print("\nTroubleshooting:")
    print("1. Check camera cable is properly connected")
    print("2. Enable camera in raspi-config")
    print("3. Reboot after enabling camera")
