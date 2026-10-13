#!/usr/bin/env python3
"""Off-device test of LoRa WiFi provisioning (station -> field), no hardware needed.

Simulates two RYLR998 modules talking to each other and runs the REAL code on both ends:
station display_station/field_link.py and field src/lora_handler.py + src/field_provision.py.
The WiFi "apply" step is a stub that records what it was asked to save.

    python3 tests/test_wifi_provisioning.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "display_station")]

import secure_link  # noqa: E402
from secure_link import LinkError, open_sealed, seal  # noqa: E402


# ---------------------------------------------------------------- fake RYLR998 pair
class FakePort:
    """One side of a simulated RYLR998: AT commands in, '+OK' and '+RCV=' lines out."""

    def __init__(self, name):
        self.name, self.peer, self.rx, self.timeout = name, None, b"", 1
        self.drop_next_sends = 0     # simulate messages lost in the air
        self.sent = []

    @property
    def in_waiting(self):
        return len(self.rx)

    def read(self, n=1):
        out, self.rx = self.rx[:n], self.rx[n:]
        return out

    def reset_input_buffer(self):
        self.rx = b""

    def write(self, data):
        for cmd in data.decode().split("\r\n"):
            if not cmd:
                continue
            if cmd.startswith("AT+SEND="):
                _addr, length, msg = cmd[8:].split(",", 2)
                assert int(length) == len(msg.encode()), "AT+SEND length mismatch"
                assert int(length) <= 240, "LoRa payload too long"
                self.sent.append(msg)
                self.rx += b"+OK\r\n"
                if self.drop_next_sends:
                    self.drop_next_sends -= 1
                else:
                    self.peer.rx += f"+RCV=0,{length},{msg},-42,11\r\n".encode()
            else:
                self.rx += b"+OK\r\n"

    def close(self):
        pass


def make_field_lora(port):
    import lora_handler
    fake_serial = types.SimpleNamespace(Serial=lambda **kw: port)
    lora_handler.serial = fake_serial
    lora_handler.time.sleep = lambda s: None          # skip the 3 s module warm-up in tests
    return lora_handler.LoRaHandler()


def station_read(port, link):
    """Mimic the station's check_for_message: cut each +RCV payload by its length."""
    data, port.rx = port.rx.decode(), b""
    for line in data.replace("\r", "").split("\n"):
        if line.startswith("+RCV="):
            _a, ln, rest = line[5:].split(",", 2)
            link.handle(rest[:int(ln)])


# ---------------------------------------------------------------- harness
class Rig:
    def __init__(self, tmp, field_key=None, apply_fails=None, give_up=30):
        self.tmp = tmp
        key = secure_link.load_key(os.path.join(tmp, ".kit_key"))
        fkey_path = os.path.join(tmp, ".kit_key")
        if field_key is not None:
            fkey_path = os.path.join(tmp, "field_key")
            with open(fkey_path, "w") as f:
                f.write(field_key.hex())
        self.key = key
        self.st_port, self.fd_port = FakePort("station"), FakePort("field")
        self.st_port.peer, self.fd_port.peer = self.fd_port, self.st_port
        self.applied = os.path.join(tmp, "applied.jsonl")
        stub = os.path.join(tmp, "apply_stub.py")
        with open(stub, "w") as f:
            f.write("import sys,json\nd=sys.stdin.read()\n"
                    f"open({self.applied!r},'a').write(d+'\\n')\n"
                    + (f"print({apply_fails!r},file=sys.stderr);sys.exit(1)\n" if apply_fails else "print('saved')\n"))
        import field_link
        import field_provision
        self.link = field_link.FieldLink(
            self.st_port, key_path=os.path.join(tmp, ".kit_key"),
            request_file=os.path.join(tmp, "req.json"), result_file=os.path.join(tmp, "res.json"),
            counter_file=os.path.join(tmp, "counter"), retry_seconds=0, give_up_seconds=give_up)
        self.lora = make_field_lora(self.fd_port)
        assert self.lora.lora_available
        self.prov = field_provision.FieldProvisioner(
            self.lora, key_path=fkey_path, apply_cmd=[sys.executable, stub],
            state_file=os.path.join(tmp, "last"), result_file=os.path.join(tmp, "last_result.json"))

    def request(self, ssid, psk):
        with open(os.path.join(self.tmp, "req.json"), "w") as f:
            json.dump({"ssid": ssid, "psk": psk}, f)

    def step(self, field_listens=True):
        self.link.tick()
        if field_listens:
            for m in self.lora.poll_messages():
                self.prov.handle(m["message"])
        station_read(self.st_port, self.link)

    def result(self):
        with open(os.path.join(self.tmp, "res.json")) as f:
            return json.load(f)

    def applied_list(self):
        if not os.path.exists(self.applied):
            return []
        return [json.loads(l) for l in open(self.applied) if l.strip()]


def fresh_dir():
    d = tempfile.mkdtemp(prefix="sp_prov_")
    with open(os.path.join(d, ".kit_key"), "w") as f:
        f.write(os.urandom(32).hex())
    return d


# ---------------------------------------------------------------- tests
def test_crypto():
    k, k2 = os.urandom(32), os.urandom(32)
    big = {"c": 99999, "s": "Ñandú-Farm WiFi 🐔 extra-long-n", "p": "x" * 63}
    msg = seal(k, "PROV", big)
    assert len(msg) <= 240, len(msg)
    assert open_sealed(k, msg, "PROV") == big
    for bad in (msg[:-2] + ("A" if msg[-2] != "A" else "B") + msg[-1],      # tampered
                msg.replace("PROV,", "PACK,")):                               # wrong type
        try:
            open_sealed(k, bad, "PROV")
            raise AssertionError("tampered/mistyped message accepted")
        except LinkError:
            pass
    try:
        open_sealed(k2, msg, "PROV")
        raise AssertionError("wrong kit key accepted")
    except LinkError:
        pass
    assert "x" * 8 not in msg and "Farm" not in msg, "plaintext visible on the radio"
    print(f"  crypto ok (worst-case message {len(msg)}/240 bytes)")


def test_happy_path():
    r = Rig(fresh_dir())
    r.request("FarmNet", "hunter2hunter2")
    for _ in range(5):
        r.step()
    assert r.result()["state"] == "done", r.result()
    assert r.applied_list() == [{"ssid": "FarmNet", "psk": "hunter2hunter2"}]
    assert not os.path.exists(os.path.join(r.tmp, "req.json")), "password left on disk"
    assert "hunter2" not in open(os.path.join(r.tmp, "res.json")).read()
    print("  happy path ok")


def test_field_busy_then_listens():
    """Field is mid-detection for a while (not polling); its own sends clear the serial buffer."""
    r = Rig(fresh_dir())
    r.request("FarmNet", "password123")
    r.link.tick()                          # station sends #1 -> sits in field's buffer
    r.lora.send_message("HEARTBEAT,ok,12:00")   # field sends before polling: must NOT lose it
    for _ in range(3):
        r.step()
    assert r.result()["state"] == "done", r.result()
    assert len(r.applied_list()) == 1
    print("  message kept across a field send (old reset_input_buffer bug) ok")


def test_lost_messages_and_lost_ack():
    r = Rig(fresh_dir())
    r.request("FarmNet", "password123")
    r.st_port.drop_next_sends = 3          # first 3 sends lost in the air
    r.fd_port.drop_next_sends = 1          # field's first acknowledgement lost too
    for _ in range(10):
        r.step()
    assert r.result()["state"] == "done", r.result()
    assert len(r.applied_list()) == 1, "WiFi applied more than once on retries"
    print(f"  retries ok (station sent {len(r.st_port.sent)}x, applied once)")


def test_replay_ignored():
    r = Rig(fresh_dir())
    r.request("FarmNet", "password123")
    for _ in range(3):
        r.step()
    old = r.st_port.sent[0]
    r.request("NewNet", "password456")
    for _ in range(3):
        r.step()
    assert [a["ssid"] for a in r.applied_list()] == ["FarmNet", "NewNet"]
    r.fd_port.rx += f"+RCV=0,{len(old)},{old},-40,10\r\n".encode()   # attacker replays old message
    for m in r.lora.poll_messages():
        r.prov.handle(m["message"])
    assert [a["ssid"] for a in r.applied_list()] == ["FarmNet", "NewNet"], "replay was applied"
    print("  replay of an old message ignored ok")


def test_wrong_kit_key():
    r = Rig(fresh_dir(), field_key=os.urandom(32), give_up=0.3)
    r.request("FarmNet", "password123")
    t0 = time.time()
    while time.time() - t0 < 1:
        r.step()
    assert r.result()["state"] == "failed" and r.applied_list() == []
    print("  other kit's key -> rejected, station reports failure ok")


def test_apply_failure_reported():
    r = Rig(fresh_dir(), apply_fails="WiFi password must be 8-63 characters")
    r.request("FarmNet", "short")
    for _ in range(3):
        r.step()
    res = r.result()
    assert res["state"] == "failed" and "8-63" in res["error"], res
    print("  field-side error shown on the station ok")


def test_parse_with_commas_and_split_reads():
    """Alert payloads contain commas; serial reads can split a line in two."""
    lora = make_field_lora(FakePort("x"))
    line = "+RCV=0,24,PREDATOR,fox,12:00,C,0.9,-40,9\r\n"
    lora._split_rcv(line[:13])
    assert lora._pending == []
    lora._split_rcv(line[13:])
    m = lora._pending[0]
    assert m["message"] == "PREDATOR,fox,12:00,C,0.9" and m["rssi"] == -40 and m["snr"] == 9, m
    print("  +RCV parsing (commas in payload, split reads) ok")


if __name__ == "__main__":
    cwd = tempfile.mkdtemp(prefix="sp_cwd_")
    os.makedirs(os.path.join(cwd, "data", "logs"))
    os.chdir(cwd)
    tests = [test_crypto, test_happy_path, test_field_busy_then_listens, test_lost_messages_and_lost_ack,
             test_replay_ignored, test_wrong_kit_key, test_apply_failure_reported,
             test_parse_with_commas_and_split_reads]
    for t in tests:
        print(t.__name__)
        t()
    shutil.rmtree(cwd, ignore_errors=True)
    print(f"\nALL {len(tests)} TESTS PASSED")
