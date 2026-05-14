#!/bin/bash
# Setup script for Home Display Station Raspberry Pi

set -e

echo "=========================================="
echo "SpotPredator - Home Display Setup"
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
    i2c-tools \
    git

# Install Python packages (display station only needs these)
echo ""
echo "Step 3: Installing Python packages..."
pip3 install pyserial adafruit-circuitpython-ssd1306 adafruit-blinka pillow

# Enable interfaces
echo ""
echo "Step 4: Enabling hardware interfaces..."
echo "You need to enable:"
echo "  - I2C"
echo "  - Serial Port (enable port, disable console)"
echo ""
read -p "Open raspi-config now? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo raspi-config
fi

# Test I2C
echo ""
echo "Step 5: Testing I2C devices..."
if command -v i2cdetect &> /dev/null; then
    echo "Scanning I2C bus (looking for OLED at 0x3C or 0x3D)..."
    i2cdetect -y 1 || true
fi

echo ""
echo "=========================================="
echo "✅ Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Reboot: sudo reboot"
echo "2. Connect hardware:"
echo "   - RYLR998 LoRa module to UART"
echo "   - OLED display to I2C"
echo "3. Test components:"
echo "   - python3 scripts/test_lora.py"
echo "   - i2cdetect -y 1 (check OLED)"
echo "4. Run display: python3 display_station/main.py"
echo ""
