"""Encrypted + authenticated LoRa messages between the station and the field device.

Used for the one thing that must never go over the radio in plain text: the field device's WiFi
credentials (station -> field "PROV", field -> station "PACK" acknowledgement).

Each kit has its own 32-byte secret key, written to BOTH devices' SD cards at prep time
(`scripts/make_kit_key.py`) and never committed to git. Standard library only (no extra packages
on the Pi Zero): HMAC-SHA256 in counter mode as the stream cipher + a truncated HMAC-SHA256 tag
(encrypt-then-MAC, separate derived keys).

Wire format (fits the RYLR998's 240-byte payload):  <TYPE>,<base64url(nonce | ciphertext | tag)>
Replay protection: every message carries a counter that must be HIGHER than the last accepted one.
"""
import base64
import hashlib
import hmac
import json
import os

KEY_FILE = ".kit_key"           # 64 hex chars, in the install dir on both devices (chmod 600)
NONCE_LEN, TAG_LEN = 8, 16
MAX_PAYLOAD = 240               # RYLR998 AT+SEND limit


class LinkError(Exception):
    """Message could not be decoded: wrong kit key, tampered, malformed or a replay."""


def load_key(path=KEY_FILE):
    with open(path) as f:
        key = bytes.fromhex(f.read().strip())
    if len(key) != 32:
        raise ValueError(f"{path} must hold a 32-byte key (64 hex characters)")
    return key


def _subkey(key, label):
    return hmac.new(key, label, hashlib.sha256).digest()


def _keystream(key, nonce, n):
    out, block = b"", 0
    while len(out) < n:
        out += hmac.new(key, nonce + block.to_bytes(4, "big"), hashlib.sha256).digest()
        block += 1
    return out[:n]


def seal(key, kind, payload):
    """Encrypt + sign a dict -> 'KIND,<b64>' string ready for AT+SEND."""
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    nonce = os.urandom(NONCE_LEN)
    ct = bytes(a ^ b for a, b in zip(data, _keystream(_subkey(key, b"enc"), nonce, len(data))))
    tag = hmac.new(_subkey(key, b"mac"), kind.encode() + nonce + ct, hashlib.sha256).digest()[:TAG_LEN]
    msg = f"{kind},{base64.urlsafe_b64encode(nonce + ct + tag).decode().rstrip('=')}"
    if len(msg) > MAX_PAYLOAD:
        raise LinkError(f"message too long for LoRa ({len(msg)} > {MAX_PAYLOAD} bytes)")
    return msg


def open_sealed(key, message, kind):
    """Verify + decrypt a 'KIND,<b64>' string -> dict. Raises LinkError if anything is off."""
    head, _, body = message.partition(",")
    if head != kind or not body:
        raise LinkError("not a %s message" % kind)
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except Exception:
        raise LinkError("bad encoding")
    if len(raw) < NONCE_LEN + TAG_LEN + 2:
        raise LinkError("too short")
    nonce, ct, tag = raw[:NONCE_LEN], raw[NONCE_LEN:-TAG_LEN], raw[-TAG_LEN:]
    want = hmac.new(_subkey(key, b"mac"), kind.encode() + nonce + ct, hashlib.sha256).digest()[:TAG_LEN]
    if not hmac.compare_digest(tag, want):
        raise LinkError("authentication failed (wrong kit key or tampered message)")
    data = bytes(a ^ b for a, b in zip(ct, _keystream(_subkey(key, b"enc"), nonce, len(ct))))
    try:
        return json.loads(data)
    except ValueError:
        raise LinkError("bad payload")


class Counter:
    """Monotonic message counter persisted to a small file (survives reboots)."""

    def __init__(self, path):
        self.path = path

    def get(self):
        try:
            with open(self.path) as f:
                return int(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0

    def set(self, value):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            f.write(str(value))
        os.replace(tmp, self.path)
