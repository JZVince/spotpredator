"""Field side of LoRa WiFi provisioning.

The station sends this device its WiFi name + password over LoRa ("PROV" message, encrypted with
the per-kit key — see secure_link.py). We verify it, save the network with NetworkManager through
a small root-owned helper (scripts/apply_wifi.py, installed as /usr/local/sbin/spotpredator-apply-wifi
with a sudoers rule), and reply "PACK" so the station can show the result.

Saved networks are ADDED, never deleted: the field device keeps every network it knows and joins
whichever is in range, so changing WiFi can't lock it out. Nothing here runs a server — it only
looks at LoRa messages the detection loop already receives.
"""
import json
import logging
import os
import subprocess

from secure_link import Counter, LinkError, load_key, open_sealed, seal

logger = logging.getLogger(__name__)

APPLY_CMD = ["sudo", "-n", "/usr/local/sbin/spotpredator-apply-wifi"]
STATE_FILE = "data/.prov_last"   # last accepted counter (replay protection)
RESULT_FILE = "data/.prov_last_result.json"


class FieldProvisioner:
    def __init__(self, lora, key_path=".kit_key", apply_cmd=APPLY_CMD, state_file=STATE_FILE,
                 result_file=RESULT_FILE):
        self.lora = lora
        self.apply_cmd = apply_cmd
        self.last = Counter(state_file)
        self.result_file = result_file
        self.key = None
        if os.path.exists(key_path):
            try:
                self.key = load_key(key_path)
            except Exception as e:
                logger.error(f"Kit key unreadable ({e}) — WiFi provisioning over LoRa disabled")
        else:
            logger.info("No kit key (.kit_key) — WiFi provisioning over LoRa disabled")

    def handle(self, message):
        """Process one received LoRa message. Returns True if it was a provisioning message."""
        if not message.startswith("PROV,"):
            return False
        if self.key is None:
            logger.warning("Got a WiFi provisioning message but this device has no kit key")
            return True
        try:
            req = open_sealed(self.key, message, "PROV")
            c = int(req["c"])
        except (LinkError, KeyError, ValueError, TypeError) as e:
            logger.warning(f"Rejected WiFi provisioning message: {e}")
            return True

        last = self.last.get()
        if c < last:
            logger.warning(f"Ignored old WiFi provisioning message #{c} (already at #{last})")
            return True
        if c == last:
            # The station is retrying because our acknowledgement was lost — re-send the same result.
            self._reply(self._load_result(c))
            return True

        ok, err = self._apply(req.get("s", ""), req.get("p", ""))
        result = {"c": c, "ok": ok, "e": err[:60]}
        self.last.set(c)
        try:
            with open(self.result_file, "w") as f:
                json.dump(result, f)
        except OSError:
            pass
        logger.info(f"📶 WiFi provisioning #{c}: {'saved' if ok else 'FAILED: ' + err}")
        self._reply(result)
        return True

    def _apply(self, ssid, psk):
        try:
            r = subprocess.run(self.apply_cmd, input=json.dumps({"ssid": ssid, "psk": psk}),
                               capture_output=True, text=True, timeout=60)
        except Exception as e:
            return False, str(e)
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or f"exit {r.returncode}").strip().splitlines()[-1]
        return True, ""

    def _load_result(self, c):
        try:
            with open(self.result_file) as f:
                res = json.load(f)
            if res.get("c") == c:
                return res
        except (OSError, ValueError):
            pass
        return {"c": c, "ok": True, "e": ""}

    def _reply(self, result):
        try:
            self.lora.send_message(seal(self.key, "PACK", result))
        except Exception as e:
            logger.error(f"Could not send WiFi provisioning acknowledgement: {e}")
