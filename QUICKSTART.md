# SpotPredator Quick Start Guide

Get your predator detection system running in 30 minutes!

## What You Need

✅ 2x Raspberry Pi Zero 2 W (with microSD cards)
✅ All hardware from your shopping list
✅ 1x NPN transistor + 1kΩ resistor (only missing item!)
✅ Computer with SD card reader

---

## Step 1: Prepare SD Cards (10 minutes)

**For BOTH Raspberry Pis:**

1. Download Raspberry Pi OS Lite:
   - https://www.raspberrypi.com/software/operating-systems/
   - Choose "Raspberry Pi OS Lite (64-bit)"

2. Flash to SD card using Raspberry Pi Imager
   - Enable SSH
   - Set WiFi credentials
   - Set hostname (field-detector / home-display)

3. Insert SD card into Pi

---

## Step 2: Initial Setup (5 minutes per Pi)

**For BOTH Raspberry Pis:**

```bash
# SSH into Pi
ssh pi@field-detector.local  # or home-display.local

# Clone/copy project
cd ~
git clone <your-repo> spotpredator
# OR: copy files via scp

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

## Step 3: Hardware Wiring (15 minutes)

### Field Detector

See [WIRING.md](WIRING.md) for details. Quick checklist:

- [ ] Camera: Connect with 15-22 pin cable to CSI port
- [ ] RTC: VCC→3.3V, GND→GND, SDA→Pin3, SCL→Pin5
- [ ] LoRa: VCC→3.3V, GND→GND, RXD→Pin8, TXD→Pin10
- [ ] Buzzer: Build transistor circuit (see WIRING.md)
- [ ] Power: 12V battery → DC-DC converter → Pi USB

### Home Display

- [ ] LoRa: VCC→3.3V, GND→GND, RXD→Pin8, TXD→Pin10
- [ ] OLED: VCC→3.3V, GND→GND, SDA→Pin3, SCL→Pin5
- [ ] Power: 5V USB adapter

---

## Step 4: Test Components (5 minutes per Pi)

**Field Detector:**

```bash
cd ~/spotpredator

# Test camera
python3 scripts/test_camera.py
# Should save test_camera.jpg

# Test LoRa
python3 scripts/test_lora.py
# Should send test message

# Test buzzer (CAREFUL!)
python3 scripts/test_buzzer.py
# Should hear beeps

# Check I2C devices
i2cdetect -y 1
# Should show 0x68 (RTC)
```

**Home Display:**

```bash
cd ~/spotpredator

# Test LoRa
python3 scripts/test_lora.py
# Should receive message from field detector

# Check I2C
i2cdetect -y 1
# Should show 0x3C or 0x3D (OLED)
```

---

## Step 5: Run the System!

**On Field Detector:**

```bash
cd ~/spotpredator
python3 src/main.py
```

You should see:
```
SpotPredator - Farm Animal Predator Detection System
✅ All components initialized
Starting detection loop...
```

**On Home Display:**

```bash
cd ~/spotpredator
python3 display_station/main.py
```

You should see:
```
SpotPredator - Home Display Station
✅ LoRa initialized
✅ OLED display initialized
Display station running...
```

---

## Step 6: Test End-to-End

1. Point field detector camera at a dog/cat photo on your phone
2. Watch for detection in field detector logs
3. Check home display - should show alert!

---

## Troubleshooting

### Camera Not Working
```bash
# Enable camera
sudo raspi-config
# Interface Options → Camera → Enable
sudo reboot

# Test
libcamera-hello
```

### LoRa Not Working
```bash
# Enable serial
sudo raspi-config
# Interface Options → Serial Port
#   Login shell: No
#   Serial port hardware: Yes
sudo reboot

# Check port exists
ls -l /dev/serial0
```

### I2C Not Working
```bash
# Enable I2C
sudo raspi-config
# Interface Options → I2C → Enable
sudo reboot

# Scan bus
i2cdetect -y 1
```

### No Detections
- Lower confidence threshold in config.yaml
- Check camera view (should see clearly)
- Test with obvious targets (photos of dogs/cats)
- Check lighting (works best in daylight)

### Too Many False Alarms
- Raise confidence threshold in config.yaml
- Adjust camera angle
- Add ignore classes in config

---

## Configuration Tips

Edit `config.yaml`:

```yaml
detector:
  confidence_threshold: 0.65  # Lower = more sensitive
                              # Higher = fewer false alarms

detection:
  check_interval: 5  # Seconds between checks
                     # Lower = faster response, more power
                     # Higher = saves power

hardware:
  lora:
    frequency: 915  # 915 for US/Canada
                    # 868 for Europe
```

---

## Running on Startup (Optional)

**Create systemd service:**

```bash
# Field detector
sudo nano /etc/systemd/system/spotpredator.service
```

```ini
[Unit]
Description=SpotPredator Field Detector
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/spotpredator
ExecStart=/usr/bin/python3 /home/pi/spotpredator/src/main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
# Enable and start
sudo systemctl enable spotpredator
sudo systemctl start spotpredator

# Check status
sudo systemctl status spotpredator

# View logs
sudo journalctl -u spotpredator -f
```

Do the same for home display with `display_station/main.py`.

---

## Next Steps

Once everything works:

1. **Adjust Settings**: Tune confidence threshold based on results
2. **Add Solar**: Connect solar panel + charge controller for 24/7 operation
3. **Weatherproofing**: Get waterproof enclosure for field device
4. **Test Range**: Walk around farm to verify LoRa coverage
5. **Monitor Logs**: Check `data/logs/` for detection history

---

## Support

- **Logs**: Check `data/logs/spotpredator.log`
- **Saved Images**: See `data/detections/`
- **Test Individual Parts**: Use test scripts in `scripts/`
- **Wiring**: See [WIRING.md](WIRING.md) for diagrams
- **Full Docs**: See [README.md](README.md)

---

**Remember**: This is your first Raspberry Pi + AI project!

- Start simple - get basic detection working first
- Test each component separately
- Don't worry about perfection
- Adjust and improve over time

Happy predator detecting! 🦅🐕🐈
