#!/usr/bin/env python3
"""Test buzzer circuit"""
import sys
sys.path.insert(0, 'src')

from buzzer_handler import BuzzerHandler
import logging
import time

logging.basicConfig(level=logging.INFO)

print("=" * 50)
print("Buzzer Test")
print("=" * 50)
print("\n⚠️  WARNING: Make sure transistor circuit is wired correctly!")
print("   Direct connection will NOT work and may damage GPIO\n")

input("Press Enter to continue...")

buzzer = BuzzerHandler(gpio_pin=27)

if buzzer.gpio_available:
    print("✅ Buzzer initialized")
    print()

    print("Test 1: Single beep...")
    buzzer.beep(count=1, beep_duration=0.2)
    time.sleep(1)

    print("Test 2: Three beeps...")
    buzzer.beep(count=3, beep_duration=0.2, pause_duration=0.2)
    time.sleep(1)

    print("Test 3: Long buzz (2 seconds)...")
    buzzer.buzz(duration=2)

    print("\n✅ Buzzer test complete")
    print("If you didn't hear anything, check wiring")

    buzzer.cleanup()
else:
    print("❌ GPIO not available")
