#!/bin/bash
# WiFi reconnect for the field device — gentle, low-load recovery.
#
# Runs ONCE PER HOUR, on the hour, via cron:
#   0 * * * * /bin/bash /home/pi/spotpredator/scripts/wifi_reconnect.sh >> /home/pi/spotpredator/data/logs/wifi_reconnect.log 2>&1
#
# Why hourly (not every few minutes): the old per-minute rescan approach put too much heat/CPU
# pressure on the Pi Zero (alongside the detection service) and could crash the device. This
# device is outdoors and disconnected most of the time anyway, so a single gentle nudge once an
# hour is plenty. And unlike the old "--rescan no" version — which read a stale scan cache and
# kept skipping forever — this actually recovers a wedged WiFi radio by restarting
# NetworkManager, which is what a full reboot did for us manually, but far lighter.
#
# Notes confirmed on this device:
#   - WiFi power-save is OFF (not the wedge cause).
#   - Connection profile "home-wifi" has autoconnect=yes, autoconnect-retries=0 (forever),
#     yet NetworkManager still occasionally stalls and previously needed a reboot to recover.
#
# What it does each run:
#   1. If the internet is reachable -> do nothing (cheap ping, NO WiFi scan).
#   2. If offline -> restart NetworkManager ONCE. That re-initializes the WiFi driver, forces a
#      fresh scan, and lets autoconnect reconnect. One quick action, not a sustained scan loop.

LOG_PREFIX="$(date '+%Y-%m-%d %H:%M:%S')"

# 1) Already online? Nothing to do. (Cheap check — no WiFi scan, no load.)
if ping -c 1 -W 3 8.8.8.8 &>/dev/null; then
    exit 0
fi

# 2) Offline — give NetworkManager a single gentle kick to recover a possibly-wedged radio.
echo "$LOG_PREFIX | Offline — restarting NetworkManager to recover WiFi..."
sudo systemctl restart NetworkManager

# Give it time to re-init the radio, scan, and reconnect via autoconnect.
sleep 30

# Report the outcome.
if ping -c 1 -W 3 8.8.8.8 &>/dev/null; then
    echo "$LOG_PREFIX | Recovered — internet reachable after NetworkManager restart"
else
    echo "$LOG_PREFIX | Still offline after restart — will retry next hour"
fi
