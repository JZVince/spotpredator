#!/usr/bin/env python3
"""Create the secret key that pairs one station with one field device ("kit key").

Run once per kit, then put the SAME .kit_key file in the install folder of BOTH devices
(/home/pi/spotpredator/.kit_key, owner pi, chmod 600). It encrypts the WiFi details the station
sends the field device over LoRa. Never commit it (.gitignore covers it) and don't reuse it
across kits.

    python3 scripts/make_kit_key.py              # writes ./.kit_key
    python3 scripts/make_kit_key.py --out PATH
"""
import argparse
import os
import secrets
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=".kit_key")
ap.add_argument("--force", action="store_true", help="overwrite an existing key (breaks pairing!)")
a = ap.parse_args()

if os.path.exists(a.out) and not a.force:
    sys.exit(f"{a.out} already exists — the devices are probably already paired with it. "
             "Use --force only if you will copy the new key to BOTH devices.")
fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(secrets.token_hex(32) + "\n")
print(f"Kit key written to {a.out} (permissions 600).")
print("Copy this same file to BOTH the station and the field device:  /home/pi/spotpredator/.kit_key")
