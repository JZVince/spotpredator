#!/bin/bash
# WiFi reconnect for the field device — light recovery + persistent diagnostics.
#
# Runs ONLY at night (21:00, 21:15, 21:30), when the user brings the device INDOORS. There is NO
# daytime run: out in the field the device is off-grid and WiFi simply won't reconnect (LoRa is
# the field link), so checking during the day is pointless — it just adds heat + log noise.
#   0,15,30 21 * * * /bin/bash /home/pi/spotpredator/scripts/wifi_reconnect.sh >> /home/pi/spotpredator/data/logs/wifi_reconnect.log 2>&1
# At 21:00 and 21:15 it tries to restore WiFi (NM restart); if still offline by the 2nd try it
# REBOOTS (the only reliable wedge fix, and safe now because the device is indoors). 21:30 = final
# catch. History: was hourly, then */20 for the 2026-07-06 diagnostic phase (cooked Pi to 80°C).
#
# --- ROOT CAUSE (finally identified 2026-07-06) --------------------------------------------
# The BCM43430 WiFi firmware's internal ROAMING ENGINE was the culprit. At marginal signal the
# firmware attempts back-to-back re-association, which can HALT the chip firmware — a full WiFi
# "wedge" where the driver can't even scan (looks identical to "out of range": visible=NO).
# FIX = disable the roaming engine: /etc/modprobe.d/brcmfmac.conf -> `options brcmfmac roamoff=1`
# (verify: `sudo cat /sys/module/brcmfmac/parameters/roamoff` == 1). This PREVENTS the wedge.
# Ref: github.com/twpure110/pi-zero2w-wifi-brcmfmac-fix. Also keep >=10s between reconnect tries.
#
# --- WHY NO DRIVER-RELOAD RECOVERY ----------------------------------------------------------
# `modprobe -r brcmfmac` CANNOT unload on this board — returns "Module is in use" even with NM +
# wpa_supplicant stopped, wlan0 down, p2p iface deleted, and roamoff=1 set (combo WiFi/BT driver
# never releases its refcount on a running system). So a TRUE wedge can only be cleared by a
# REBOOT. There is intentionally no driver-reload step.
#
# --- WHAT THIS SCRIPT DOES ------------------------------------------------------------------
#   online?            -> reset fail counter, exit.
#   offline?           -> log snapshot (temp/signal/NM), then L1: restart NetworkManager.
#   still offline?     -> increment a consecutive-failure counter. If ENABLE_REBOOT=1 and we've
#                         failed REBOOT_AFTER_FAILS times in a row, REBOOT (the only wedge fix).
#   No SSID "in range" gating: a wedge can't scan, so visible=NO can't distinguish wedge from
#   out-of-range. Signal/visibility is logged for diagnosis only, never used to skip recovery.
#
# --- SUDOERS (/etc/sudoers.d/wifi-reconnect) ------------------------------------------------
#   pi ALL=(ALL) NOPASSWD: /bin/systemctl restart NetworkManager
#   pi ALL=(ALL) NOPASSWD: /bin/systemctl reboot        # only needed if ENABLE_REBOOT=1
# (The old modprobe/ip rules are no longer used — driver reload was removed.)

# --- Paths ----------------------------------------------------------------------------------
# The cron-redirected log (wifi_reconnect.log) is TRUNCATED weekly by main.py's Monday cleanup.
# The persistent diagnostics log below is NOT in that cleanup list, so it survives reboots and
# the weekly wipe — this is the file to read after a field failure.
DIAG_LOG="/home/pi/spotpredator/data/logs/wifi_diagnostics.log"

TARGET_SSID="ogarward-24"   # home SSID — logged for diagnostics only (signal strength), not gating
PING_IP="8.8.8.8"

# --- Reboot escalation config ---------------------------------------------------------------
# A true WiFi wedge on this board can ONLY be cleared by a reboot (the driver can't be unloaded).
# We DO NOT want a full OS reboot while the device is working OUTSIDE during the day (it would
# interrupt detection for no reason — the field link is LoRa, not WiFi). So reboot is restricted
# to a NIGHT WINDOW: the user brings the device indoors ~9pm, and that's exactly when WiFi should
# come back. If NM still can't reconnect inside that window, a reboot is warranted and welcome.
ENABLE_REBOOT=1             # reboot IS allowed inside the night window (device is indoors then).
REBOOT_WINDOW_START="21:00" # earliest a reboot may happen (user brings device in ~9pm)
REBOOT_WINDOW_END="21:30"   # latest — the script only runs 21:00/21:15/21:30 (see cron), indoors
REBOOT_AFTER_FAILS=2        # 21:00 try, 21:15 try; if still offline by the 2nd, reboot (21:30 = final catch)
FAILCOUNT_FILE="/home/pi/spotpredator/data/logs/.wifi_failcount"  # reset to 0 whenever online

# --- Helpers --------------------------------------------------------------------------------
# diag() -> timestamped line to the persistent diagnostics file AND stdout (cron log).
diag() { echo "$(date '+%Y-%m-%d %H:%M:%S') | $1" | tee -a "$DIAG_LOG"; }

online() { ping -c 1 -W 3 "$PING_IP" &>/dev/null; }

# Log a state snapshot for post-mortem diagnosis: temp, whether the home SSID is visible + its
# signal strength (0-100), and when NetworkManager last (re)started. Diagnostic only — does not
# affect behavior. Signal helps distinguish a weak/marginal link from a healthy one after the fact.
snapshot() {
    local temp ssid_line visible signal nm_ts
    temp="$(vcgencmd measure_temp 2>/dev/null)"
    nm_ts="$(systemctl show NetworkManager -p ActiveEnterTimestamp --value 2>/dev/null)"
    ssid_line="$(nmcli -t -f SSID,SIGNAL device wifi list --rescan yes 2>/dev/null | grep "^${TARGET_SSID}:")"
    if [ -n "$ssid_line" ]; then
        visible="YES"; signal="${ssid_line##*:}"   # part after the last colon = SIGNAL value
    else
        visible="NO"; signal="n/a"
    fi
    diag "  state: ${temp} | SSID '$TARGET_SSID' visible=$visible signal=${signal} | NM_last_start=$nm_ts"
}

# --- 0) Already online? Nothing to do (cheap, no scan, no disk write) ------------------------
if online; then
    rm -f "$FAILCOUNT_FILE"   # healthy → clear the consecutive-failure counter
    exit 0
fi

# --- Offline: log a snapshot, then attempt recovery. NO range/wedge gating. -----------------
# We do NOT gate on SSID visibility anymore. A wedged BCM43430 can't complete a scan, so a wedge
# looks identical to "out of range" (both give visible=NO). The scan/signal is logged for
# diagnostics only. We just try to recover; if genuinely out of range, an NM restart is harmless.
diag "OFFLINE detected — beginning recovery."
snapshot   # logs temp + SSID visibility + signal + NM start time (diagnostic only)

# --- L1: restart NetworkManager -------------------------------------------------------------
diag "  L1: restarting NetworkManager..."
sudo systemctl restart NetworkManager
diag "  L1: NM_last_start=$(systemctl show NetworkManager -p ActiveEnterTimestamp --value)"
sleep 30   # >=10s spacing (brcmfmac firmware halts under back-to-back re-association)
if online; then
    diag "  RECOVERED at L1 (NetworkManager restart)."
    rm -f "$FAILCOUNT_FILE"
    exit 0
fi

# --- L1 (NM restart) failed. The only remaining wedge fix is a REBOOT -----------------------
# There is deliberately no driver-reload step: `modprobe -r brcmfmac` can't unload on this board
# (see header). We reboot only after REBOOT_AFTER_FAILS consecutive failures AND only inside the
# night window, so a reboot never happens while the device is out working during the day.
now_hm="$(date '+%H:%M')"

# Fresh count each night: the counter file persists across runs/reboots, but the script only runs
# at night (21:00/21:15/21:30) and does NOT run during the day, so a stale count from a previous
# night can survive and cause a reboot on the VERY FIRST run tonight. On the first run of the
# night (== REBOOT_WINDOW_START), reset the counter to 0 so tonight starts clean. Then it takes
# two ACTUAL consecutive failures the same night (21:00 -> 1, 21:15 -> 2) to reach the threshold.
if [ "$now_hm" = "$REBOOT_WINDOW_START" ]; then
    rm -f "$FAILCOUNT_FILE"
fi

fails=0
[ -f "$FAILCOUNT_FILE" ] && fails=$(cat "$FAILCOUNT_FILE" 2>/dev/null || echo 0)
fails=$((fails + 1))
echo "$fails" > "$FAILCOUNT_FILE"
diag "  L1 failed. Consecutive failed recoveries: $fails (reboot threshold: $REBOOT_AFTER_FAILS)."

# Are we inside the allowed night window? (string HH:MM compares fine for a same-day window.)
in_window=0
if [[ "$now_hm" > "$REBOOT_WINDOW_START" || "$now_hm" == "$REBOOT_WINDOW_START" ]] \
   && [[ "$now_hm" < "$REBOOT_WINDOW_END" || "$now_hm" == "$REBOOT_WINDOW_END" ]]; then
    in_window=1
fi

if [ "$ENABLE_REBOOT" = "1" ] && [ "$fails" -ge "$REBOOT_AFTER_FAILS" ] && [ "$in_window" = "1" ]; then
    diag "  In night window ($REBOOT_WINDOW_START-$REBOOT_WINDOW_END) & threshold reached — rebooting to clear a wedged WiFi driver (device is indoors; only reliable fix)."
    rm -f "$FAILCOUNT_FILE"
    sudo systemctl reboot
elif [ "$ENABLE_REBOOT" = "1" ] && [ "$fails" -ge "$REBOOT_AFTER_FAILS" ]; then
    diag "  STILL OFFLINE & threshold reached, but NOT in night window ($REBOOT_WINDOW_START-$REBOOT_WINDOW_END) — NOT rebooting (device likely still outside/working). Will reboot in the window if still failing."
else
    diag "  STILL OFFLINE. ***No action beyond NM restart.***"
fi
exit 1
