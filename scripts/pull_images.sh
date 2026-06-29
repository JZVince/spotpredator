#!/bin/bash
# Pull scan and detection images from the field Pi into a dated folder.
#
# Configure via environment variables (override the defaults below):
#   FIELD_PI    SSH target for the field Pi      (default: pi@raspberrypi.local)
#   REMOTE_DIR  Project path on the Pi           (default: /home/pi/spotpredator)
#   DEST_BASE   Local folder to save images into (default: ./pulled_images)
#
# Example:
#   FIELD_PI=pi@192.168.1.50 DEST_BASE=~/Desktop/scans ./pull_images.sh

FIELD_PI="${FIELD_PI:-pi@raspberrypi.local}"
REMOTE_DIR="${REMOTE_DIR:-/home/pi/spotpredator}"
DEST_BASE="${DEST_BASE:-./pulled_images}"

DATE_FOLDER=$(date +"%Y_%m_%d")
DEST="$DEST_BASE/$DATE_FOLDER"

mkdir -p "$DEST"

echo "Pulling images from $FIELD_PI to $DEST..."

scp -r "$FIELD_PI:$REMOTE_DIR/data/scans" "$DEST/"
scp -r "$FIELD_PI:$REMOTE_DIR/data/detections" "$DEST/"
scp -r "$FIELD_PI:$REMOTE_DIR/data/detections_clean" "$DEST/"

echo "Done. Images saved to $DEST"
