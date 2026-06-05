#!/bin/bash
# WiFi reconnect script for field device
# Runs every 5 minutes via cron. Scans for known network and reconnects if visible.
# cron: */5 * * * * /bin/bash /home/pi/spotpredator/scripts/wifi_reconnect.sh >> /home/pi/spotpredator/data/logs/wifi_reconnect.log 2>&1

WIFI_SSID="$(nmcli -t -f NAME connection show --active | head -1)"
LOG_PREFIX="$(date '+%Y-%m-%d %H:%M:%S')"

# Already connected and internet reachable — nothing to do
if ping -c 1 -W 3 8.8.8.8 &>/dev/null; then
    exit 0
fi

# Scan for available networks
SCAN=$(nmcli -t -f SSID device wifi list --rescan yes 2>/dev/null)

# Get the configured connection name from nmcli
CONNECTION=$(nmcli -t -f NAME,TYPE connection show | grep wireless | cut -d: -f1 | head -1)

if [ -z "$CONNECTION" ]; then
    echo "$LOG_PREFIX | No wireless connection profile found, skipping"
    exit 0
fi

# Get the SSID of that connection
TARGET_SSID=$(nmcli -t -f 802-11-wireless.ssid connection show "$CONNECTION" 2>/dev/null | cut -d: -f2)

if [ -z "$TARGET_SSID" ]; then
    echo "$LOG_PREFIX | Could not determine target SSID, skipping"
    exit 0
fi

# Check if target SSID is visible in scan results
if echo "$SCAN" | grep -q "$TARGET_SSID"; then
    echo "$LOG_PREFIX | Network '$TARGET_SSID' visible, attempting reconnect..."
    nmcli connection up "$CONNECTION" &>/dev/null
    sleep 20
    if ping -c 1 -W 3 8.8.8.8 &>/dev/null; then
        echo "$LOG_PREFIX | Reconnected successfully"
    else
        echo "$LOG_PREFIX | Reconnect attempted but internet still unreachable"
    fi
else
    echo "$LOG_PREFIX | Network '$TARGET_SSID' not visible, skipping reconnect"
fi
