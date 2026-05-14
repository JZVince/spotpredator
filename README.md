# SpotPredator

An AI-powered farm predator detection system built on Raspberry Pi Zero 2 W. SpotPredator uses a custom-trained EfficientNetB0 computer vision model to identify predators in real time, and alerts you wirelessly via LoRa radio — no internet required in the field. Free from monthly subscription plans.

---

## Overview

Farm animals face constant threats from hawks, eagles, foxes, coyotes, and other predators. SpotPredator provides an automated, low-cost, low-power monitoring solution that runs entirely offline in the field and notifies you at home the moment a threat is detected.

This is my first Raspberry Pi, computer vision, and soldering project — built from scratch with no prior hardware experience. There were cold joints, wrong pins, a buzzer that beeped all night, and a computer vision model that silently collapsed for two weeks before I caught it. I learned more from those mistakes than from anything that worked first try. The project is far from perfect — if you spot a bug, a better approach, or have suggestions, feel free to open an issue or pull request. Feedback is always welcome.

The system consists of two devices. Field detector code lives in `src/`, display station code lives in `display_station/`.

**Field Detector** — deployed outdoors near your animals
- Captures images every 15 seconds using a Raspberry Pi camera
- Runs AI inference locally using a TFLite classification model or YOLOV8 Nano object detection model
- Sounds a buzzer alarm on detection
- Transmits alerts wirelessly to your home via LoRa radio
- Sends heartbeat status updates every 30 minutes to keep you updated on system status
- Operates on a schedule (default 6:00 AM – 9:01 PM)
- Runs on battery power — no WiFi required

**Display Station** — sits indoors on your desk
- Receives LoRa alerts from the field device
- Shows predator type, confidence, and time on an OLED display
- Flashes the screen continuously until the threat clears
- Sends you an immediate email alert on predator detection
- Receives and logs a field scan summary report via LoRa
- Generates a Perl-based daily report with ASCII confidence graph

---

## Hardware

### Both Devices
| Component | Details |
|-----------|---------|
| Raspberry Pi Zero 2 W | Main compute unit |
| microSD card | 32GB+ recommended |
| 5V 2.5A power supply | For indoor/bench use |
| RYLR998 LoRa Module | 915MHz (US) / 868MHz (EU), up to 15km range |
| SFM-27-W Piezo Buzzer | 3-27V, loud alarm |
| 2N2222 NPN Transistor | Buzzer drive circuit |
| 1kΩ resistor | Transistor base resistor |

### Field Detector (additional)
| Component | Details |
|-----------|---------|
| Arducam Camera Module 3 | IMX708, autofocus, 12MP |
| 15-to-22 pin FFC cable | Required for Pi Zero camera connector |
| DS3231 RTC Module | Real-time clock with CR2032 battery |
| 12V LiFePO4 Battery (10Ah) | Field power supply |
| 12V → 5V DC-DC Converter (3A) | Powers the Pi from battery |
| 30W solar panel |

### Display Station (additional)
| Component | Details |
|-----------|---------|
| SSD1306 OLED Display | 128x64, I2C, 0.96 inch |

---

## AI Model

SpotPredator uses a custom-trained **EfficientNetB0** classifier converted to TensorFlow Lite for on-device inference.

- **Input**: 224x224 RGB image
- **Classes**: `background`, `poultry`, `predator`
- **Confidence threshold**: 85% (configurable)
- **Inference time**: ~1-2 seconds on Pi Zero 2 W
- **Model size**: ~16MB

The model is trained using transfer learning from ImageNet weights. Training is done in Google Colab with GPU acceleration and exported as a `.tflite` file for deployment.

> **Note**: The model file is not included in this repository due to size. In addition, I am trying a second custom model with YOLOV8 Nano object detection model to see which one is better. Something I have learned is that, before you deploy your model into your device, test it with some new test images locally and make sure you are getting reasonable result. My classification model was producing 66% background confidence for every scans and I didn't know about it until two weeks later. What's funny is that none of my poultries got killed for those two weeks. So I thought great, my device is working perfectly without any false positives.

---

## Installation

### 1. Flash Raspberry Pi OS

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to flash **Raspberry Pi OS Lite (64-bit)** to your microSD card. Enable SSH and configure WiFi in the imager settings before flashing.

> **WiFi tip**: Raspberry Pi zero 2 W works best on 2.4GHz networks. If you have trouble connecting, disable WiFi 6 mode and roaming assistant on your router, and separate your 2.4GHz and 5GHz networks. If you have remote connection issue, check these settings.

### 2. Enable Required Interfaces

```bash
sudo raspi-config
```

Enable the following:
- Camera (libcamera)
- I2C
- Serial Port — **disable serial console, keep serial hardware enabled**

Then reboot:
```bash
sudo reboot
```

### 3. Clone the Repository

```bash
git clone https://github.com/yourusername/spotpredator.git
cd spotpredator
```

### 4. Create Virtual Environment and Install Dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

System packages also required:
```bash
sudo apt install -y python3-picamera2 i2c-tools libio-socket-ssl-perl perl
```

### 5. Configure Email (Display Station only)

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

### 6. Configure the System

Edit `config.yaml` to match your setup:

```yaml
detector:
  confidence_threshold: 0.85   # Raise to reduce false positives

hardware:
  lora:
    frequency: 915              # 915 for US, 868 for EU
    network_id: 18              # Change if multiple LoRa networks nearby
  buzzer:
    gpio_pin: 27

detection:
  check_interval: 15            # Seconds between scans
  cooldown_period: 180          # Seconds before re-alerting same predator
  schedule_enabled: true
  start_hour: 6                 # Start scanning at 6:00 AM
  end_hour: 21                  # Stop scanning at 9:01 PM
  end_minute: 1
```

### 7. Place Your Trained Model

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

### 8. Set Up Auto-Start Services

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

### 9. Set Up Daily Perl Report (Display Station only)

```bash
crontab -e
```

Add:
```
0 22 * * * perl /home/pi/spotpredator/scripts/daily_report.pl > /home/pi/spotpredator/data/logs/perl_report.log 2>&1
```

> The cron schedule `0 22 * * *` means: minute 0, hour 22 (10:00 PM), every day.

---

## Wiring

See [WIRING.md](WIRING.md) for full pin diagrams for both devices.

> **Soldering tip**: If soldering for the first time, flux is your best friend. It cleans metal surfaces, helps solder flow properly, and prevents cold joints.

---

## Troubleshooting

### Cannot SSH into Pi — connection refused or times out
- Before assuming hardware failure (SD card, cable, Pi itself), check your router first
- Pi Zero 2 W only supports **2.4GHz WiFi** — if your router broadcasts a combined 2.4/5GHz network, the Pi may fail to connect
- I'm not saying 5GHz won't work completely, but sure I had a lot issue with it.
- Disable **WiFi 6 (802.11ax)** mode on your router — Pi Zero 2 W does not support it
- Disable **roaming assistant** or **band steering** on your router — these features can kick pi devices out of connection due to low signal strength.
- Separate your 2.4GHz and 5GHz into two distinct networks and connect the Pi to the 2.4GHz one explicitly. A lot older devices work the best with 2.4GHz, same result for my BambuLab Printer and regular printer.
- After changing router settings, re-flash the SD card with the correct WiFi credentials and try again

### Camera not working
- Reseat the ribbon cable firmly — this is the most common cause
- Run `libcamera-hello` to test
- Enable camera in `raspi-config` and reboot

### LoRa not responding
- Verify VCC is on **Pin 17 (3.3V)**, not Pin 18 (GPIO)
- Check TX/RX are crossed between Pi and LoRa module
- Ensure serial console is disabled in `raspi-config`
- Both devices must use the same frequency and network ID

### RTC resetting to year 2000
- CR2032 battery contact is loose — press firmly and bend the spring contact
- Replace battery if old
- Reseat all RTC jumper wires

### Buzzer beeping continuously
- Usually caused by RTC I2C errors interfering with GPIO
- Reseat RTC module wires and reboot
- Check `buzzer_enabled` in `config.yaml`
- Possible soldering issue

### Station Display Device WiFi not reconnecting
- Run `nmcli connection show` and verify autoconnect is `yes`
- Disable WiFi power saving: `sudo iw dev wlan0 set power_save off`
- Add `autoconnect-retries=0` to your `.nmconnection` file for unlimited retries

### Model producing identical confidence scores for every scan
- Test with a known image: does inference output change with different inputs?
- If probabilities are identical regardless of input — model has collapsed, retrain required
- Common causes of model collapse:
  - **Learning rate too high during fine-tuning** — destroys pretrained ImageNet weights early in training, leaving the model unable to generalize
  - **Too many background images** — if background heavily outnumbers other classes, the model learns to always predict background as a safe default
  - **Imbalanced dataset** — aim for roughly equal class sizes; background should be a small fraction (~10-15%) of total images
  - **Augmentation too aggressive** — heavy distortion during training can prevent the model from learning meaningful features
- Before deploying any retrained model, always test it locally with a few known images and confirm confidence scores change with different inputs

---

## Future Plans

- Retrain model with images captured directly from field camera for better real-world accuracy
- Training YOLOv8 Nano with custom dataset for a better object detection model
- Expand predator classes with more species-specific training data
- Add night vision / IR camera support for dawn and dusk detection
- Improve enclosure weatherproofing with ventilation and heatsink - This has to do with 3D printing design
- Add physical button on display station to acknowledge and clear alerts
- Explore ESP32 as a lower-cost alternative for the display station
- Write an app for quick installation.

---

## License

MIT License — feel free to modify and adapt for your needs.

---

Built for protecting farm animals from predators using accessible, low-cost hardware and open-source AI.
