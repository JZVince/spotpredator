#!/usr/bin/env python3
"""Test LoRa communication between two devices"""
import sys
sys.path.insert(0, 'src')

from lora_handler import LoRaHandler
import logging
import time

logging.basicConfig(level=logging.INFO)

print("=" * 50)
print("LoRa Communication Test")
print("=" * 50)
print()

lora = LoRaHandler(port="/dev/serial0", baud_rate=115200, network_id=18, frequency=915)

if lora.lora_available:
    print("✅ LoRa initialized")
    print()

    # Send test message
    print("Sending test message...")
    if lora.send_message("TEST_MESSAGE_FROM_SPOTPREDATOR"):
        print("✅ Message sent")
        print()
        print("Check the other device to see if it received the message")
    else:
        print("❌ Failed to send message")

    # Wait for incoming messages
    print("\nListening for messages for 10 seconds...")
    for i in range(10):
        msg = lora.receive_message(timeout=1)
        if msg:
            print(f"✅ Received: {msg['message']}")
            break
        print(f"  Waiting... ({i+1}/10)")
    else:
        print("⚠️  No messages received")

    lora.cleanup()
else:
    print("❌ LoRa not available")
    print("\nTroubleshooting:")
    print("1. Check RYLR998 is connected to GPIO 14/15")
    print("2. Enable serial port in raspi-config")
    print("3. Disable serial console in raspi-config")
    print("4. Reboot after changes")
