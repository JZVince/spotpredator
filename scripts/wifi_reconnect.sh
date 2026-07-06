#!/bin/bash
# WiFi reconnect for the field device — gentle, ESCALATING recovery with persistent diagnostics.
#
# Runs EVERY 20 MINUTES via cron (was hourly; sped up 2026-07-06 for the diagnostic phase — to
# gather signal-strength data faster and attempt recovery sooner):
#   */20 * * * * /bin/bash /home/pi/spotpredator/scripts/wifi_reconnect.sh >> /home/pi/spotpredator/data/logs/wifi_reconnect.log 2>&1
# NOTE ON THRASH RISK: at 20-min cadence WITH recovery, a device left OUT OF RANGE all day would
# attempt NM restart / driver reload up to 3x/hour. That's far lighter than the old 3-min loop
# that overheated the Pi, but heavier than hourly. Acceptable for now while we diagnose; revisit
# once the signal data tells us whether the real problem is distance (range) or a driver wedge.
#
# --- BACKGROUND (why this script looks the way it does) -------------------------------------
# Long debugging session (2026-07-05) established the following, so we don't re-chase ghosts:
#   * The old log showed two lines with the SAME timestamp ("restarting" then "still offline"),
#     which looked like the sleep was skipped. IT WASN'T — LOG_PREFIX was captured ONCE at the
#     top and reused, so both echoes printed the start time. Bench test proved the script really
#     runs ~42s (30s sleep + ping timeouts) and DOES restart NM. Fixed here: every log line now
#     stamps its own real time via log().
#   * NM restart works from cron / stripped env / by hand (all verified). sudo NOPASSWD works.
#   * Many field "offline" hours were simply OUT OF RANGE (no AP) — nothing to fix.
#   * BUT a real NM restart + 30s wait was still observed to leave the device offline sometimes,
#     i.e. an occasional genuine wedge a service restart can't clear. Hence escalation below.
#
# --- ESCALATION (least disruptive first; only escalate if the network is actually IN RANGE) --
#   L1  restart NetworkManager                     (fixes NM/wpa_supplicant wedges)
#   L2  reload brcmfmac kernel WiFi driver          (fixes a frozen driver, no reboot)
#   We DO NOT auto-reboot. If L1+L2 fail while the SSID is visible, we log a strong "REBOOT
#   RECOMMENDED" marker to the persistent diagnostics log for a human to act on. Auto-reboot was
#   rejected because out-in-the-field the device is legitimately out of range most of the time,
#   and rebooting hourly for that would be pure harm.
#
# Escalation only runs when the target SSID is VISIBLE in a scan. If it's not visible, the device
# is simply out of range — we log that and exit, touching nothing.
#
# --- SUDOERS ---------------------------------------------------------------------------------
# As of 2026-07-05 the broad `NOPASSWD: ALL` rule was REMOVED (tightened). `pi` now only has
# passwordless sudo for the specific commands this script needs, in /etc/sudoers.d/wifi-reconnect:
#   pi ALL=(ALL) NOPASSWD: /bin/systemctl restart NetworkManager
#   pi ALL=(ALL) NOPASSWD: /usr/sbin/modprobe -r brcmfmac
#   pi ALL=(ALL) NOPASSWD: /usr/sbin/modprobe brcmfmac
#   pi ALL=(ALL) NOPASSWD: /usr/sbin/ip link set wlan0 down     # <-- ADD for L2 (see below)
# Paths must be EXACT now that the blanket rule is gone (modprobe/ip are in /usr/sbin here, not
# /sbin). Everything else requires the pi password.

# --- Paths ----------------------------------------------------------------------------------
# Regular hourly log (cron redirects stdout here) — NOTE: this file is TRUNCATED weekly by the
# detection service's Monday cleanup (main.py). Do not rely on it for long-term diagnosis.
#
# Persistent diagnostics log — written to ONLY when something notable happens (offline event,
# restart, escalation). NOT in main.py's weekly-cleanup list, so it survives both reboots and
# the weekly wipe. This is the file to read after a field failure.
DIAG_LOG="/home/pi/spotpredator/data/logs/wifi_diagnostics.log"

TARGET_SSID="ogarward-24"   # SSID of the home-wifi profile (profile name "home-wifi")
PING_IP="8.8.8.8"

# --- Helpers --------------------------------------------------------------------------------
# log() -> hourly log (stdout, captured by cron). Fresh timestamp EACH call (the old bug fix).
log()  { echo "$(date '+%Y-%m-%d %H:%M:%S') | $1"; }
# diag() -> persistent diagnostics file AND stdout, so a notable event lands in both.
diag() { echo "$(date '+%Y-%m-%d %H:%M:%S') | $1" | tee -a "$DIAG_LOG"; }

online() { ping -c 1 -W 3 "$PING_IP" &>/dev/null; }

# Snapshot of useful state for post-mortem diagnosis.
# 2026-07-06: added SIGNAL strength — this is the key discriminator between the two theories:
#   * DISTANCE (device ~100m out, marginal WiFi): offline events happen at WEAK signal. The
#     driver is healthy; it just can't hold a link that far. No fix but antenna/range.
#   * DRIVER WEDGE: offline persists even at STRONG signal, and the kernel log shows brcmfmac
#     errors. THAT is what a restart/reload/reboot should fix.
# So we log the signal of TARGET_SSID on every offline event. A run of "offline + weak signal"
# says range; "offline + strong signal" says wedge. We are NOT changing recovery behavior yet —
# just gathering the data to decide correctly.
snapshot() {
    local temp ssid_line ssid_visible signal nm_ts
    temp="$(vcgencmd measure_temp 2>/dev/null)"
    # Grab the SSID's scan line WITH its signal (0-100). One scan, reused for both checks.
    ssid_line="$(nmcli -t -f SSID,SIGNAL device wifi list --rescan yes 2>/dev/null | grep "^${TARGET_SSID}:")"
    if [ -n "$ssid_line" ]; then
        ssid_visible="YES"
        signal="${ssid_line##*:}"   # part after the last colon = SIGNAL value
    else
        ssid_visible="NO"
        signal="n/a"
    fi
    nm_ts="$(systemctl show NetworkManager -p ActiveEnterTimestamp --value 2>/dev/null)"
    diag "  state: ${temp} | SSID '$TARGET_SSID' visible=$ssid_visible signal=${signal} | NM_last_start=$nm_ts"
    # Export for the caller to branch on (unchanged logic — diagnosis only).
    SSID_VISIBLE="$ssid_visible"
}

# --- 0) Already online? Nothing to do (cheap, no scan, no disk write) ------------------------
if online; then
    exit 0
fi

# --- Offline: begin diagnosis + escalation --------------------------------------------------
diag "OFFLINE detected — beginning recovery."
snapshot   # sets SSID_VISIBLE, logs temp + visibility + NM start time

# If the network isn't even in range, there is nothing a restart can fix. Log and stop.
if [ "$SSID_VISIBLE" = "NO" ]; then
    diag "  '$TARGET_SSID' NOT in range → out of range, not a wedge. No action taken."
    exit 0
fi

# --- L1: restart NetworkManager -------------------------------------------------------------
diag "  L1: restarting NetworkManager (SSID is in range, so a wedge is plausible)..."
sudo systemctl restart NetworkManager
rc=$?
diag "  L1: restart returned rc=$rc; NM_last_start=$(systemctl show NetworkManager -p ActiveEnterTimestamp --value)"
sleep 30
if online; then
    diag "  RECOVERED at L1 (NetworkManager restart)."
    exit 0
fi

# --- L2: reload the brcmfmac kernel WiFi driver ---------------------------------------------
diag "  L1 failed (still offline). L2: reloading brcmfmac WiFi driver..."
# Bring the interface DOWN first: `modprobe -r brcmfmac` fails with "Module is in use" while
# wlan0 is up and carrying traffic (observed on the bench). Downing wlan0 releases the driver
# so it can actually unload. NM autoconnect brings wlan0 back up after the driver reloads.
# Absolute paths: cron's minimal PATH may not include /usr/sbin, so bare command names can be
# "command not found" under cron even though they work in an interactive shell.
sudo /usr/sbin/ip link set wlan0 down 2>>"$DIAG_LOG"
sleep 2
sudo /usr/sbin/modprobe -r brcmfmac 2>>"$DIAG_LOG"
sleep 3
sudo /usr/sbin/modprobe brcmfmac 2>>"$DIAG_LOG"
sleep 30
if online; then
    diag "  RECOVERED at L2 (brcmfmac driver reload)."
    exit 0
fi

# --- L1 + L2 both failed, and the SSID WAS visible → genuine deep wedge ----------------------
# We do NOT auto-reboot. Record a strong marker + a fresh snapshot for a human to decide.
diag "  L2 failed — STILL OFFLINE with SSID in range. ***REBOOT RECOMMENDED*** (deep wedge)."
snapshot
diag "  (No auto-reboot by design. If you see repeated REBOOT RECOMMENDED markers, consider"
diag "   enabling an L3 reboot or investigating the brcmfmac driver.)"
exit 1
