#!/bin/bash
# Setup script for SpotPredator Field Detector
# Run this once on a fresh Raspberry Pi OS Lite install

set -e

INSTALL_DIR="/home/pi/spotpredator"

echo "=========================================="
echo "SpotPredator - Field Detector Setup"
echo "=========================================="
echo ""

# Check if running on Raspberry Pi
if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
    echo "Warning: This doesn't appear to be a Raspberry Pi"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Step 1: System update
echo "Step 1: Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Step 2: System dependencies
echo ""
echo "Step 2: Installing system dependencies..."
sudo apt install -y \
    python3-pip \
    python3-full \
    python3-venv \
    python3-picamera2 \
    i2c-tools \
    git

# Step 3: Python virtual environment
echo ""
echo "Step 3: Setting up Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"
pip install --upgrade pip
pip install -r "$INSTALL_DIR/requirements.txt"
deactivate

# Step 4: Create data directories
echo ""
echo "Step 4: Creating data directories..."
mkdir -p "$INSTALL_DIR/data/logs"
mkdir -p "$INSTALL_DIR/data/detections"
mkdir -p "$INSTALL_DIR/data/scans"

# Step 5: Enable hardware interfaces
echo ""
echo "Step 5: Hardware interfaces"
echo "You need to enable the following in raspi-config:"
echo "  - Camera (libcamera)"
echo "  - I2C"
echo "  - Serial Port: disable login shell, keep hardware enabled"
echo ""
read -p "Open raspi-config now? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo raspi-config
fi

# Step 6: Install systemd service
echo ""
echo "Step 6: Installing systemd service..."
sudo cp "$INSTALL_DIR/scripts/spotpredator.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable spotpredator
echo "Service installed and enabled (will start on next boot)"

# Step 7: Place model reminder
echo ""
echo "Step 7: AI Model"
echo "Place your trained model files in $INSTALL_DIR/models/:"
echo "  - spotpredator_classifier.tflite"
echo "  - classifier_labels.txt"
echo ""
if [ ! -f "$INSTALL_DIR/models/spotpredator_classifier.tflite" ]; then
    echo "Warning: Model file not found. Detection will not work until model is placed."
fi

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Place your model in $INSTALL_DIR/models/"
echo "2. Check wiring: see WIRING.md"
echo "3. Test components:"
echo "   source $INSTALL_DIR/venv/bin/activate"
echo "   python3 $INSTALL_DIR/tests/test_camera.py"
echo "   python3 $INSTALL_DIR/tests/test_lora.py"
echo "4. Reboot to start the service: sudo reboot"
echo ""
