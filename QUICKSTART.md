# SpotPredator Quick Start Guide

Get your predator detection system running in 30 minutes!

## What You Need

- 2x Raspberry Pi Zero 2 W (with microSD cards)
- All hardware from the README shopping list
- 1x NPN transistor + 1kΩ resistor
- Computer with SD card reader

---

## Step 1: Prepare SD Cards (10 minutes)

**For BOTH Raspberry Pis:**

1. Download Raspberry Pi OS Lite:
   - https://www.raspberrypi.com/software/operating-systems/
   - Choose "Raspberry Pi OS Lite (64-bit)"

2. Flash to SD card using Raspberry Pi Imager
   - Enable SSH
   - Set WiFi credentials
   - Set hostname (`field-detector` / `home-display`)

3. Insert SD card into Pi

---

## Step 2: Initial Setup (5 minutes per Pi)

**For BOTH Raspberry Pis:**

```bash
# SSH into Pi
ssh pi@field-detector.local  # or home-display.local

# Clone project
cd ~
git clone https://github.com/JZVince/spotpredator.git spotpredator
cd spotpredator

# Run setup script
chmod +x scripts/setup_*.sh
./scripts/setup_field.sh      # For field detector
# OR
./scripts/setup_display.sh    # For home display

# Reboot
sudo reboot
```

---

## Step 3: Configure Email (Display Station only)

Create a `.env` file in the `display_station/` directory:

```bash
cat > display_station/.env << 'EOF'
EMAIL_ADDRESS=your@gmail.com
EMAIL_PASSWORD=your_app_password
WIFI_CONNECTION=your-nmcli-connection-name
EOF
```

To get a Gmail App Password: Google Account → Security → 2-Step Verification → App Passwords.

To find your WiFi connection name:
```bash
nmcli connection show
```

---

## Step 4: Hardware Wiring (15 minutes)

### Field Detector

See [WIRING.md](WIRING.md) for details. Quick checklist:

- [ ] Camera: Connect with 15-22 pin cable to CSI port
- [ ] RTC: VCC→3.3V, GND→GND, SDA→Pin3, SCL→Pin5
- [ ] LoRa: VCC→3.3V, GND→GND, RXD→Pin8, TXD→Pin10
- [ ] Buzzer: Build transistor circuit (see WIRING.md)
- [ ] Power: 12V battery → DC-DC converter → Pi USB

### Display Station

- [ ] LoRa: VCC→3.3V, GND→GND, RXD→Pin8, TXD→Pin10
- [ ] OLED: VCC→3.3V, GND→GND, SDA→Pin3, SCL→Pin5
- [ ] Buzzer: Build transistor circuit (see WIRING.md)
- [ ] Power: 5V USB adapter

---

## Step 5: Place Your Trained Model

Copy your trained TFLite model and labels to the `models/` directory:

```
models/
├── spotpredator_classifier.tflite
└── classifier_labels.txt
```

`classifier_labels.txt` should contain one class per line:
```
background
poultry
predator
```

> Before deploying, always test your model locally with a few known images to confirm it is producing reasonable results.

---

## Step 6: Test Components (5 minutes per Pi)

**Field Detector:**

```bash
cd ~/spotpredator

# Test camera
python3 tests/test_camera.py
# Should save test_camera.jpg

# Test LoRa
python3 tests/test_lora.py
# Should send test message

# Check I2C devices
i2cdetect -y 1
# Should show 0x68 (RTC)
```

**Display Station:**

```bash
cd ~/spotpredator

# Test LoRa
python3 tests/test_lora.py
# Should receive message from field detector

# Test email
python3 tests/test_email.py
# Should send a test email

# Check I2C
i2cdetect -y 1
# Should show 0x3C or 0x3D (OLED)
```

---

## Step 7: Set Up Auto-Start Services

**Field Detector:**

```bash
sudo cp scripts/spotpredator.service /etc/systemd/system/
sudo systemctl enable spotpredator
sudo systemctl start spotpredator
```

**Display Station:**

```bash
sudo cp scripts/display_station.service /etc/systemd/system/
sudo systemctl enable display_station
sudo systemctl start display_station
```

Check status:

```bash
sudo systemctl status spotpredator       # or display_station
sudo journalctl -u spotpredator -f       # live logs
```

---

## Step 8: Set Up Daily Perl Report (Display Station only)

```bash
crontab -e
```

Add:
```
0 22 * * * perl /home/pi/spotpredator/scripts/daily_report.pl > /home/pi/spotpredator/data/logs/perl_report.log 2>&1
```

> The cron schedule `0 22 * * *` means: minute 0, hour 22 (10:00 PM), every day.

---

## Step 9: Test End-to-End

1. Point field detector camera at a predator photo on your phone
2. Watch for detection in field detector logs
3. Check display station — should show alert on OLED and sound buzzer
4. Check your email — alert email should arrive within seconds

---

## Troubleshooting

### Camera Not Working
```bash
sudo raspi-config
# Interface Options → Camera → Enable
sudo reboot

libcamera-hello
```

### LoRa Not Working
```bash
sudo raspi-config
# Interface Options → Serial Port
#   Login shell: No
#   Serial port hardware: Yes
sudo reboot

ls -l /dev/serial0
```

### I2C Not Working
```bash
sudo raspi-config
# Interface Options → I2C → Enable
sudo reboot

i2cdetect -y 1
```

### No Detections
- Lower confidence threshold in `config.yaml`
- Test model locally before deploying — identical confidence scores every scan means model has collapsed
- Check camera is capturing correctly (check `data/scans/` for saved images)
- Check lighting — works best in daylight

### Too Many False Alarms
- Raise confidence threshold in `config.yaml`
- Adjust camera angle
- Retrain model with more representative images

---

## Configuration Tips

Edit `config.yaml`:

```yaml
detector:
  confidence_threshold: 0.85  # Raise to reduce false positives

detection:
  check_interval: 15          # Seconds between scans
  schedule_enabled: true
  start_hour: 6               # Start at 6:00 AM
  end_hour: 21                # Stop at 9:01 PM
  end_minute: 1

hardware:
  lora:
    frequency: 915            # 915 for US, 868 for EU
```

---

## Support

- **Logs**: `data/logs/spotpredator.log` (field) / `data/logs/display_station.log` (display)
- **Scan Images**: `data/scans/` — labeled with class and confidence
- **Detection Images**: `data/detections/`
- **Field Messages**: `data/logs/field_messages.log`
- **Daily Report**: `data/logs/daily_report.txt`
- **Wiring**: [WIRING.md](WIRING.md)
- **Full Docs**: [README.md](README.md)
