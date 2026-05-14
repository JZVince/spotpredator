#!/usr/bin/env python3
"""Test camera functionality"""
import sys
sys.path.insert(0, 'src')

from camera_handler import CameraHandler
import logging

logging.basicConfig(level=logging.INFO)

print("=" * 50)
print("Camera Test")
print("=" * 50)
print()

camera = CameraHandler(resolution=(640, 480))

if camera.start():
    print("✅ Camera started successfully")

    # Capture test image
    print("\nCapturing test image...")
    if camera.capture_and_save("test_camera.jpg"):
        print("✅ Image saved as test_camera.jpg")
    else:
        print("❌ Failed to capture image")

    camera.stop()
else:
    print("❌ Failed to start camera")
    print("\nTroubleshooting:")
    print("1. Check camera cable is properly connected")
    print("2. Enable camera in raspi-config")
    print("3. Reboot after enabling camera")
