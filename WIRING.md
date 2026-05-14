# SpotPredator Wiring Guide

Complete pin connection guide for Raspberry Pi Zero 2 W hardware.

## ⚠️ Important Safety Notes

1. **Power OFF** the Pi before connecting/disconnecting anything
2. **Double-check polarity** (VCC, GND) before powering on
3. **Buzzer requires transistor** - direct GPIO connection will NOT work
4. **3.3V devices only** - Do NOT connect 5V devices to GPIO pins

## Pin Reference

Raspberry Pi Zero 2 W uses **BCM (Broadcom)** pin numbering for GPIO.

```
         3.3V  [1]  [2]  5V
    GPIO 2/SDA [3]  [4]  5V
    GPIO 3/SCL [5]  [6]  GND
        GPIO 4 [7]  [8]  GPIO 14 (TX)
           GND [9]  [10] GPIO 15 (RX)
       GPIO 17 [11] [12] GPIO 18
       GPIO 27 [13] [14] GND
       GPIO 22 [15] [16] GPIO 23
          3.3V [17] [18] GPIO 24
       GPIO 10 [19] [20] GND
        GPIO 9 [21] [22] GPIO 25
       GPIO 11 [23] [24] GPIO 8
           GND [25] [26] GPIO 7
        GPIO 0 [27] [28] GPIO 1
        GPIO 5 [29] [30] GND
        GPIO 6 [31] [32] GPIO 12
       GPIO 13 [33] [34] GND
       GPIO 19 [35] [36] GPIO 16
       GPIO 26 [37] [38] GPIO 20
           GND [39] [40] GPIO 21
```

---

## Field Detector Wiring

### 1. Arducam Camera Module 3

**Connection**: CSI Port (between GPIO pins and HDMI port)

```
Arducam Module 3 ─── 15-to-22 pin FFC cable ─── Pi Zero 2 W CSI Port
```

**Notes:**
- Use the included 15-to-22 pin adapter cable
- Cable orientation matters - check Arducam docs
- Blue/silver strip should face specific direction
- Test with: `libcamera-hello`

### 2. DS3231 RTC Module

**I2C Connection** (I2C address: 0x68)

```
DS3231   →  Pi Zero 2 W
─────────────────────────
VCC      →  Pin 1 (3.3V)
GND      →  Pin 6 (GND)
SDA      →  Pin 3 (GPIO 2)
SCL      →  Pin 5 (GPIO 3)
```

**Notes:**
- Enable I2C in raspi-config
- Test with: `i2cdetect -y 1` (should show 0x68)

### 3. RYLR998 LoRa Module

**UART Connection**

```
RYLR998  →  Pi Zero 2 W
─────────────────────────
VCC      →  Pin 1 (3.3V)
GND      →  Pin 6 (GND)
RXD      →  Pin 8 (GPIO 14 / TX)
TXD      →  Pin 10 (GPIO 15 / RX)
```

**Critical Notes:**
- ⚠️ **RXD goes to TX**, **TXD goes to RX** (crossover!)
- ⚠️ **3.3V only** - NOT 5V!
- Enable serial port in raspi-config
- Disable serial console in raspi-config
- Test with: `python3 scripts/test_lora.py`

### 4. SFM-27-W Buzzer (with Transistor Circuit)

**⚠️ CRITICAL: Cannot connect buzzer directly to GPIO!**

You need this circuit:

```
                     +5V (Pin 2 or 4)
                      │
                      │
                  Buzzer (+)
                      │
                      │
          ┌───────────┴───────────┐
          │                       │
          │    NPN Transistor     │
          │    (BC337/S8050)      │
          │                       │
       Collector              Base ─── 1kΩ resistor ─── GPIO 27 (Pin 13)
          │                       │
          │                   Emitter
          │                       │
          └───────────────────────┴─── GND (Pin 14 or others)

Buzzer (-)  →  GND
```

**Component List:**
- 1x NPN Transistor (BC337, S8050, C815, 2N2222, or similar)
- 1x 1kΩ resistor (brown-black-red)
- 2x jumper wires

**Step-by-Step:**
1. Insert transistor into breadboard
2. Connect transistor Emitter to GND
3. Connect transistor Collector to Buzzer (+)
4. Connect Buzzer (-) to GND
5. Connect 1kΩ resistor between GPIO 27 and transistor Base
6. Connect 5V to Buzzer (+) or use transistor to switch it

**Transistor Pinout (BC337 / flat side facing you):**
```
   ┌─────┐
   │  │  │
   │  │  │
   └──┴──┘
   │  │  │
   C  B  E

C = Collector (to Buzzer +)
B = Base (to GPIO via 1kΩ)
E = Emitter (to GND)
```

### 5. Power (12V Battery)

```
12V LiFePO4 Battery
     │
     └──► DC-DC Converter (12V → 5V, 3A)
              │
              └──► Pi Zero 2 W USB Power Port
```

**Notes:**
- Verify converter is set to 5V output
- Use thick wires for high current
- Check polarity before connecting!

---

## Home Display Station Wiring

### 1. RYLR998 LoRa Module #2

**Same as field detector** - see above

```
RYLR998  →  Pi Zero 2 W
─────────────────────────
VCC      →  Pin 1 (3.3V)
GND      →  Pin 6 (GND)
RXD      →  Pin 8 (GPIO 14 / TX)
TXD      →  Pin 10 (GPIO 15 / RX)
```

### 2. ELEGOO OLED Display (SSD1306)

**I2C Connection** (I2C address: 0x3C or 0x3D)

```
OLED     →  Pi Zero 2 W
─────────────────────────
VCC/VDD  →  Pin 1 (3.3V)
GND      →  Pin 9 (GND)
SDA      →  Pin 3 (GPIO 2)
SCL      →  Pin 5 (GPIO 3)
```

**Notes:**
- Enable I2C in raspi-config
- Most OLED displays use 0x3C
- Test with: `i2cdetect -y 1`
- If 0x3D, change in display_station/main.py

### 3. Power

```
5V USB Power Supply (2.5A) → Pi Zero 2 W USB Power Port
```

---

## Complete Pin Usage Summary

### Field Detector
| Component | GPIO/Pin | Description |
|-----------|----------|-------------|
| Camera | CSI Port | Arducam Module 3 |
| RTC SDA | GPIO 2 (Pin 3) | I2C Data |
| RTC SCL | GPIO 3 (Pin 5) | I2C Clock |
| LoRa TX | GPIO 14 (Pin 8) | UART Transmit |
| LoRa RX | GPIO 15 (Pin 10) | UART Receive |
| Buzzer | GPIO 27 (Pin 13) | Via transistor |
| Power | 3.3V (Pin 1) | For LoRa, RTC |
| Power | 5V (Pin 2/4) | For buzzer |
| Ground | GND (Pin 6,9,14) | Common ground |

### Home Display
| Component | GPIO/Pin | Description |
|-----------|----------|-------------|
| OLED SDA | GPIO 2 (Pin 3) | I2C Data |
| OLED SCL | GPIO 3 (Pin 5) | I2C Clock |
| LoRa TX | GPIO 14 (Pin 8) | UART Transmit |
| LoRa RX | GPIO 15 (Pin 10) | UART Receive |
| Power | 3.3V (Pin 1) | For LoRa, OLED |
| Ground | GND (Pin 6,9) | Common ground |

---

## Testing Connections

After wiring, test each component:

```bash
# Test camera
python3 scripts/test_camera.py

# Test I2C devices (should show 0x68 and 0x3C/0x3D)
i2cdetect -y 1

# Test LoRa
python3 scripts/test_lora.py

# Test buzzer (CAREFUL - make sure transistor circuit is correct!)
python3 scripts/test_buzzer.py
```

---

## Common Issues

### Camera Not Detected
- Check cable orientation
- Check cable is fully inserted
- Enable camera in raspi-config
- Reboot after enabling

### I2C Device Not Found
- Check VCC is 3.3V (not 5V)
- Check SDA/SCL not swapped
- Enable I2C in raspi-config
- Check connections with multimeter

### LoRa Not Responding
- Check TX↔RX crossover (not TX↔TX!)
- Check 3.3V power (not 5V!)
- Enable serial, disable console in raspi-config
- Reboot after changes

### Buzzer Not Working
- Verify transistor circuit wiring
- Check transistor type (NPN, not PNP)
- Check 1kΩ resistor on base
- Test transistor with multimeter
- Verify 5V power to buzzer

---

## Quick Reference Card

Print and keep with your Pi:

```
╔══════════════════════════════════════╗
║     SpotPredator Quick Reference     ║
╠══════════════════════════════════════╣
║ Camera:  CSI Port                    ║
║ RTC:     I2C (0x68)                  ║
║ OLED:    I2C (0x3C)                  ║
║ LoRa:    UART (GPIO 14/15)           ║
║ Buzzer:  GPIO 27 + Transistor        ║
║                                      ║
║ ⚠️ 3.3V only for GPIO!               ║
║ ⚠️ Buzzer needs transistor!          ║
║ ⚠️ LoRa TX→RX crossover!             ║
╚══════════════════════════════════════╝
```
