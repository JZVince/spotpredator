#!/usr/bin/env python3
"""Save a WiFi network with NetworkManager (runs as root via sudo).

Install (field device), root-owned so the unprivileged service can't change what it does:
    sudo install -o root -g root -m 755 scripts/apply_wifi.py /usr/local/sbin/spotpredator-apply-wifi
    echo 'pi ALL=(root) NOPASSWD: /usr/local/sbin/spotpredator-apply-wifi' | sudo tee /etc/sudoers.d/spotpredator-wifi
    sudo chmod 440 /etc/sudoers.d/spotpredator-wifi

Reads {"ssid": ..., "psk": ...} as JSON on stdin (never on the command line, so the password
doesn't show up in `ps`). Updates the password if that network is already saved, otherwise adds
it. Never deletes or disconnects other networks. The newest network gets a higher autoconnect
priority so it's preferred when several known networks are in range.
"""
import json
import subprocess
import sys


def nmcli(*args):
    return subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=30)


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def main():
    try:
        req = json.loads(sys.stdin.read())
        ssid, psk = str(req["ssid"]), str(req.get("psk", ""))
    except Exception:
        fail("bad request")
    if not 1 <= len(ssid.encode()) <= 32:
        fail("WiFi name must be 1-32 bytes")
    if psk and not (8 <= len(psk) <= 63 or (len(psk) == 64 and all(c in "0123456789abcdefABCDEF" for c in psk))):
        fail("WiFi password must be 8-63 characters")

    # Find existing saved connections for this SSID.
    existing = []
    for line in nmcli("-t", "-f", "NAME,TYPE", "connection", "show").stdout.splitlines():
        name, _, ctype = line.rpartition(":")
        name = name.replace("\\:", ":")
        if ctype == "802-11-wireless":
            got = nmcli("-g", "802-11-wireless.ssid", "connection", "show", name).stdout.strip()
            if got == ssid:
                existing.append(name)

    sec = (["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk] if psk else [])
    if existing:
        for name in existing:
            args = ["connection", "modify", name, "connection.autoconnect", "yes",
                    "connection.autoconnect-priority", "10"]
            r = nmcli(*(args + sec))
            if r.returncode != 0:
                fail(r.stderr.strip() or "nmcli modify failed")
            if not psk:  # network changed to open (no password)
                nmcli("connection", "modify", name, "remove", "802-11-wireless-security")
    else:
        r = nmcli("connection", "add", "type", "wifi", "ifname", "wlan0", "con-name", f"spotpredator-{ssid}",
                  "ssid", ssid, "connection.autoconnect", "yes", "connection.autoconnect-priority", "10", *sec)
        if r.returncode != 0:
            fail(r.stderr.strip() or "nmcli add failed")
    print("saved")


if __name__ == "__main__":
    main()
