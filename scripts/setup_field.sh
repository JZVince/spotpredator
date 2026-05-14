#!/bin/bash
# Setup script for Field Detector Raspberry Pi

set -e

echo "=========================================="
echo "SpotPredator - Field Detector Setup"
echo "=========================================="
echo ""

# Check if running on Raspberry Pi
if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
    echo "⚠️  Warning: This doesn't appear to be a Raspberry Pi"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Update system
echo "Step 1: Updating system..."
sudo apt update
sudo apt upgrade -y

# Install system packages
echo ""
echo "Step 2: Installing system packages..."
sudo apt install -y \
    python3-pip \
    python3-picamera2 \
    i2c-tools \
    git \
    wget \
    unzip \
    libcap-dev

# Create virtual environment and install Python packages
echo ""
echo "Step 3: Creating virtual environment and installing Python packages..."
sudo apt install -y python3-full python3-venv
python3 -m venv ~/spotpredator/venv
source ~/spotpredator/venv/bin/activate
pip3 install -r requirements.txt

# Enable interfaces
echo ""
echo "Step 4: Enabling hardware interfaces..."
echo "You need to enable:"
echo "  - Camera"
echo "  - I2C"
echo "  - Serial Port (enable port, disable console)"
echo ""
read -p "Open raspi-config now? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo raspi-config
fi

# Download model
echo ""
echo "Step 5: Downloading AI model..."
./scripts/download_model.sh

# Create directories
echo ""
echo "Step 6: Creating data directories..."
mkdir -p data/detections data/logs

# Test I2C
echo ""
echo "Step 7: Testing I2C devices..."
if command -v i2cdetect &> /dev/null; then
    echo "Scanning I2C bus..."
    i2cdetect -y 1 || true
fi

echo ""
echo "=========================================="
echo "✅ Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Reboot: sudo reboot"
echo "2. Connect hardware (see WIRING.md)"
echo "3. Activate virtual environment first:"
echo "   source ~/spotpredator/venv/bin/activate"
echo "4. Test components:"
echo "   - python3 scripts/test_camera.py"
echo "   - python3 scripts/test_lora.py"
echo "   - python3 scripts/test_buzzer.py"
echo "5. Run detector: python3 src/main.py"
echo ""
