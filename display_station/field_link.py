"""Station side of LoRa WiFi provisioning for the field device.

A request ("send these WiFi details to the field device") is dropped into REQUEST_FILE by the
setup tool (scripts/field_wifi.py now, the station web page later). The station's main loop calls
tick(), which picks it up, encrypts it with the kit key (src/secure_link.py) and sends it over the
LoRa serial port the station already owns. Because the field device only glances at the radio
between detection cycles, we RESEND every few seconds until it acknowledges ("PACK") or we give up.
Progress/result is written to RESULT_FILE (never contains the password).
"""
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from secure_link import Counter, LinkError, load_key, open_sealed, seal  # noqa: E402

logger = logging.getLogger(__name__)

LINK_DIR = "data/field_link"
REQUEST_FILE = os.path.join(LINK_DIR, "wifi_request.json")
RESULT_FILE = os.path.join(LINK_DIR, "wifi_result.json")
COUNTER_FILE = "data/.prov_counter"
RETRY_SECONDS = 8
GIVE_UP_SECONDS = 600


class FieldLink:
    def __init__(self, lora_serial, key_path=".kit_key", request_file=REQUEST_FILE,
                 result_file=RESULT_FILE, counter_file=COUNTER_FILE,
                 retry_seconds=RETRY_SECONDS, give_up_seconds=GIVE_UP_SECONDS):
        self.serial = lora_serial
        self.request_file, self.result_file = request_file, result_file
        self.counter = Counter(counter_file)
        self.retry_seconds, self.give_up_seconds = retry_seconds, give_up_seconds
        self.job = None
        self.key = None
        if os.path.exists(key_path):
            try:
                self.key = load_key(key_path)
            except Exception as e:
                logger.error(f"Kit key unreadable ({e}) — can't send WiFi to the field device")
        else:
            logger.info("No kit key (.kit_key) — sending WiFi to the field device is disabled")

    @property
    def busy(self):
        return self.job is not None

    def _result(self, **fields):
        os.makedirs(os.path.dirname(self.result_file) or ".", exist_ok=True)
        fields["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = self.result_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(fields, f)
        os.replace(tmp, self.result_file)

    def tick(self):
        """Call often from the main loop: start a queued request / resend / time out."""
        if self.job is None and os.path.exists(self.request_file):
            self._start()
        if self.job is None:
            return
        now = time.time()
        if now - self.job["started"] > self.give_up_seconds:
            logger.warning(f"📶 Field device never confirmed WiFi #{self.job['c']} — giving up")
            self._result(state="failed", ssid=self.job["ssid"], attempts=self.job["tries"],
                         error="Field device did not answer. Is it powered on and within LoRa range?")
            self.job = None
        elif now >= self.job["next"]:
            self._send(self.job["msg"])
            self.job["tries"] += 1
            self.job["next"] = now + self.retry_seconds
            self._result(state="sending", ssid=self.job["ssid"], attempts=self.job["tries"])

    def _start(self):
        try:
            with open(self.request_file) as f:
                req = json.load(f)
        except (OSError, ValueError) as e:
            req = None
            logger.error(f"Bad WiFi request file: {e}")
        finally:
            try:
                os.remove(self.request_file)  # the password must not stay on disk
            except OSError:
                pass
        if req is None:
            return
        ssid = str(req.get("ssid", ""))
        if self.key is None:
            self._result(state="failed", ssid=ssid, error="No kit key on the station (.kit_key)")
            return
        c = self.counter.get() + 1
        try:
            msg = seal(self.key, "PROV", {"c": c, "s": ssid, "p": str(req.get("psk", ""))})
        except LinkError as e:
            self._result(state="failed", ssid=ssid, error=str(e))
            return
        self.counter.set(c)
        self.job = {"c": c, "ssid": ssid, "msg": msg, "tries": 0, "next": 0, "started": time.time()}
        logger.info(f"📶 Sending WiFi '{ssid}' to the field device (#{c})")

    def _send(self, msg):
        try:
            self.serial.write(f"AT+SEND=0,{len(msg.encode())},{msg}\r\n".encode())
        except Exception as e:
            logger.error(f"LoRa send failed: {e}")

    def handle(self, message):
        """Feed every received LoRa message here. Returns True if it was a provisioning reply."""
        if not message.startswith("PACK,"):
            return False
        if self.key is None:
            return True
        try:
            res = open_sealed(self.key, message, "PACK")
        except LinkError as e:
            logger.warning(f"Rejected field WiFi acknowledgement: {e}")
            return True
        if self.job and res.get("c") == self.job["c"]:
            ok = bool(res.get("ok"))
            logger.info(f"📶 Field device {'saved' if ok else 'could NOT save'} WiFi '{self.job['ssid']}'"
                        + ("" if ok else f": {res.get('e')}"))
            self._result(state="done" if ok else "failed", ssid=self.job["ssid"],
                         attempts=self.job["tries"], error="" if ok else res.get("e", "unknown error"))
            self.job = None
        return True
