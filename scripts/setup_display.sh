#!/bin/bash
# Setup script for SpotPredator Display Station
# Run this once on a fresh Raspberry Pi OS Lite install

set -e

INSTALL_DIR="/home/pi/spotpredator"

echo "=========================================="
echo "SpotPredator - Display Station Setup"
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
    i2c-tools \
    git \
    perl \
    libio-socket-ssl-perl \
    libauthen-sasl-perl

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

# Step 5: Configure email credentials
echo ""
echo "Step 5: Email configuration"
ENV_FILE="$INSTALL_DIR/display_station/.env"
if [ -f "$ENV_FILE" ]; then
    echo ".env file already exists, skipping."
else
    read -p "Enter your Gmail address: " email_addr
    read -p "Enter your Gmail App Password: " email_pass
    read -p "Enter your WiFi connection name (run 'nmcli connection show' to find it): " wifi_conn
    cat > "$ENV_FILE" << EOF
EMAIL_ADDRESS=$email_addr
EMAIL_PASSWORD=$email_pass
WIFI_CONNECTION=$wifi_conn
EOF
    echo ".env file created."
fi

# Step 6: Enable hardware interfaces
echo ""
echo "Step 6: Hardware interfaces"
echo "You need to enable the following in raspi-config:"
echo "  - I2C"
echo "  - Serial Port: disable login shell, keep hardware enabled"
echo ""
read -p "Open raspi-config now? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo raspi-config
fi

# Step 7: Install systemd service
echo ""
echo "Step 7: Installing systemd service..."
sudo cp "$INSTALL_DIR/scripts/display_station.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable display_station
echo "Service installed and enabled (will start on next boot)"

# Step 8: Set up daily Perl report cron job
echo ""
echo "Step 8: Setting up daily Perl report (10:00 PM cron job)..."
CRON_JOB="0 22 * * * perl $INSTALL_DIR/scripts/daily_report.pl > $INSTALL_DIR/data/logs/perl_report.log 2>&1"
# Add only if not already present
if crontab -l 2>/dev/null | grep -q "daily_report.pl"; then
    echo "Cron job already exists, skipping."
else
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "Cron job added."
fi

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Check wiring: see WIRING.md"
echo "2. Test components:"
echo "   source $INSTALL_DIR/venv/bin/activate"
echo "   python3 $INSTALL_DIR/tests/test_lora.py"
echo "   python3 $INSTALL_DIR/tests/test_email.py"
echo "3. Reboot to start the service: sudo reboot"
echo ""
