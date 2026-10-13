#!/usr/bin/env python3
"""Send WiFi details to the field device over LoRa (run ON THE STATION).

Temporary command-line version of the station web page's "Field device WiFi" form. Queues the
request for the running display_station service, which encrypts and sends it, then shows the
result. The field device can be anywhere within LoRa range — it doesn't need WiFi yet.

    cd /home/pi/spotpredator && venv/bin/python scripts/field_wifi.py
"""
import getpass
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "display_station"))
from field_link import GIVE_UP_SECONDS, REQUEST_FILE, RESULT_FILE  # noqa: E402

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if not os.path.exists(".kit_key"):
    sys.exit("No .kit_key here — create one with scripts/make_kit_key.py and copy it to both devices.")

ssid = input("Field device WiFi name: ").strip()
if not ssid:
    sys.exit("No WiFi name given.")
psk = getpass.getpass("WiFi password (leave empty for an open network): ")
if psk and getpass.getpass("Type the password again: ") != psk:
    sys.exit("Passwords don't match.")
if psk and not 8 <= len(psk) <= 63:
    sys.exit("WiFi passwords are 8-63 characters.")

os.makedirs(os.path.dirname(REQUEST_FILE), exist_ok=True)
try:
    os.remove(RESULT_FILE)
except OSError:
    pass
fd = os.open(REQUEST_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump({"ssid": ssid, "psk": psk}, f)
print("Queued. The station sends it and repeats until the field device confirms (up to "
      f"{GIVE_UP_SECONDS // 60} min)...")

deadline, last = time.time() + GIVE_UP_SECONDS + 30, None
while time.time() < deadline:
    time.sleep(2)
    try:
        with open(RESULT_FILE) as f:
            res = json.load(f)
    except (OSError, ValueError):
        if os.path.exists(REQUEST_FILE) and time.time() > deadline - GIVE_UP_SECONDS + 10:
            sys.exit("The station service hasn't picked up the request — is display_station running?")
        continue
    if res.get("state") == "sending" and res.get("attempts") != last:
        last = res.get("attempts")
        print(f"  sending... (attempt {last})")
    elif res.get("state") == "done":
        sys.exit(print(f"✓ Field device saved WiFi '{ssid}'. It will connect whenever that network is in range."))
    elif res.get("state") == "failed":
        sys.exit(f"✗ Failed: {res.get('error')}")
sys.exit("Timed out waiting for the station.")
