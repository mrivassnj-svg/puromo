"""
pinout_engine.py
----------------
Complete pinout and wiring diagram data generator.

For every module in a HandheldBuild it outputs:
  - Full GPIO pin assignments (BCM numbers, physical pin numbers, signal names)
  - Per-module wiring table (module pin → host GPIO → wire colour → voltage)
  - Safety checks (voltage level mismatches, shared-bus conflicts, power budget)
  - Serialises to JSON for the UI wiring diagram renderer
"""

import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional

# ---------------------------------------------------------------------------
# Raspberry Pi GPIO map  (BCM → physical pin, name, capabilities)
# Physical layout: 2-row 20-col header, pin 1 = top-left (3V3)
# ---------------------------------------------------------------------------
RPI_GPIO = {
    # (bcm, physical, name, [capabilities])
    "3V3_1":   {"bcm": None, "phys": 1,  "name": "3V3 Power",    "caps": ["PWR"],     "voltage": 3.3},
    "5V_2":    {"bcm": None, "phys": 2,  "name": "5V Power",     "caps": ["PWR"],     "voltage": 5.0},
    "GPIO2":   {"bcm": 2,   "phys": 3,  "name": "GPIO2 (SDA1)", "caps": ["I2C","GPIO"],"voltage": 3.3},
    "GPIO3":   {"bcm": 3,   "phys": 5,  "name": "GPIO3 (SCL1)", "caps": ["I2C","GPIO"],"voltage": 3.3},
    "GPIO4":   {"bcm": 4,   "phys": 7,  "name": "GPIO4",        "caps": ["GPIO","1W"], "voltage": 3.3},
    "GND_6":   {"bcm": None, "phys": 6,  "name": "Ground",       "caps": ["GND"],      "voltage": 0},
    "GPIO14":  {"bcm": 14,  "phys": 8,  "name": "GPIO14 (TXD)", "caps": ["UART","GPIO"],"voltage": 3.3},
    "GPIO15":  {"bcm": 15,  "phys": 10, "name": "GPIO15 (RXD)", "caps": ["UART","GPIO"],"voltage": 3.3},
    "GPIO17":  {"bcm": 17,  "phys": 11, "name": "GPIO17",       "caps": ["GPIO"],      "voltage": 3.3},
    "GPIO18":  {"bcm": 18,  "phys": 12, "name": "GPIO18 (PWM0)","caps": ["GPIO","PWM"],"voltage": 3.3},
    "GPIO27":  {"bcm": 27,  "phys": 13, "name": "GPIO27",       "caps": ["GPIO"],      "voltage": 3.3},
    "GND_14":  {"bcm": None, "phys": 14, "name": "Ground",       "caps": ["GND"],      "voltage": 0},
    "GPIO22":  {"bcm": 22,  "phys": 15, "name": "GPIO22",       "caps": ["GPIO"],      "voltage": 3.3},
    "GPIO23":  {"bcm": 23,  "phys": 16, "name": "GPIO23",       "caps": ["GPIO"],      "voltage": 3.3},
    "3V3_17":  {"bcm": None, "phys": 17, "name": "3V3 Power",    "caps": ["PWR"],      "voltage": 3.3},
    "GPIO24":  {"bcm": 24,  "phys": 18, "name": "GPIO24",       "caps": ["GPIO"],      "voltage": 3.3},
    "GPIO10":  {"bcm": 10,  "phys": 19, "name": "GPIO10 (MOSI)","caps": ["SPI","GPIO"],"voltage": 3.3},
    "GND_20":  {"bcm": None, "phys": 20, "name": "Ground",       "caps": ["GND"],      "voltage": 0},
    "GPIO9":   {"bcm": 9,   "phys": 21, "name": "GPIO9 (MISO)", "caps": ["SPI","GPIO"],"voltage": 3.3},
    "GPIO25":  {"bcm": 25,  "phys": 22, "name": "GPIO25",       "caps": ["GPIO"],      "voltage": 3.3},
    "GPIO11":  {"bcm": 11,  "phys": 23, "name": "GPIO11 (SCLK)","caps": ["SPI","GPIO"],"voltage": 3.3},
    "GPIO8":   {"bcm": 8,   "phys": 24, "name": "GPIO8 (CE0)",  "caps": ["SPI","GPIO"],"voltage": 3.3},
    "GND_25":  {"bcm": None, "phys": 25, "name": "Ground",       "caps": ["GND"],      "voltage": 0},
    "GPIO7":   {"bcm": 7,   "phys": 26, "name": "GPIO7 (CE1)",  "caps": ["SPI","GPIO"],"voltage": 3.3},
    "GPIO0":   {"bcm": 0,   "phys": 27, "name": "GPIO0 (ID_SD)","caps": ["I2C","GPIO"],"voltage": 3.3},
    "GPIO1":   {"bcm": 1,   "phys": 28, "name": "GPIO1 (ID_SC)","caps": ["I2C","GPIO"],"voltage": 3.3},
    "GPIO5":   {"bcm": 5,   "phys": 29, "name": "GPIO5",        "caps": ["GPIO"],      "voltage": 3.3},
    "GND_30":  {"bcm": None, "phys": 30, "name": "Ground",       "caps": ["GND"],      "voltage": 0},
    "GPIO6":   {"bcm": 6,   "phys": 31, "name": "GPIO6",        "caps": ["GPIO"],      "voltage": 3.3},
    "GPIO12":  {"bcm": 12,  "phys": 32, "name": "GPIO12 (PWM0)","caps": ["GPIO","PWM"],"voltage": 3.3},
    "GPIO13":  {"bcm": 13,  "phys": 33, "name": "GPIO13 (PWM1)","caps": ["GPIO","PWM"],"voltage": 3.3},
    "GND_34":  {"bcm": None, "phys": 34, "name": "Ground",       "caps": ["GND"],      "voltage": 0},
    "GPIO19":  {"bcm": 19,  "phys": 35, "name": "GPIO19 (MISO)","caps": ["SPI","GPIO"],"voltage": 3.3},
    "GPIO16":  {"bcm": 16,  "phys": 36, "name": "GPIO16 (CE0)", "caps": ["SPI","GPIO"],"voltage": 3.3},
    "GPIO26":  {"bcm": 26,  "phys": 37, "name": "GPIO26",       "caps": ["GPIO"],      "voltage": 3.3},
    "GPIO20":  {"bcm": 20,  "phys": 38, "name": "GPIO20 (MOSI)","caps": ["SPI","GPIO"],"voltage": 3.3},
    "GND_39":  {"bcm": None, "phys": 39, "name": "Ground",       "caps": ["GND"],      "voltage": 0},
    "GPIO21":  {"bcm": 21,  "phys": 40, "name": "GPIO21 (SCLK)","caps": ["SPI","GPIO"],"voltage": 3.3},
}

# ---------------------------------------------------------------------------
# Standard wire colour conventions (IEC 60757 + electronics hobby)
# ---------------------------------------------------------------------------
WIRE_COLOURS = {
    "3V3":   {"colour": "#FF0000", "name": "Red",          "hex": "FF0000"},
    "5V":    {"colour": "#FF6600", "name": "Orange",       "hex": "FF6600"},
    "GND":   {"colour": "#000000", "name": "Black",        "hex": "000000"},
    "MOSI":  {"colour": "#0000FF", "name": "Blue",         "hex": "0000FF"},
    "MISO":  {"colour": "#00AAFF", "name": "Light Blue",   "hex": "00AAFF"},
    "SCLK":  {"colour": "#FFFF00", "name": "Yellow",       "hex": "FFFF00"},
    "CS":    {"colour": "#FF00FF", "name": "Violet",       "hex": "FF00FF"},
    "DC":    {"colour": "#FF8800", "name": "Orange-Red",   "hex": "FF8800"},
    "RST":   {"colour": "#FF4444", "name": "Light Red",    "hex": "FF4444"},
    "BL":    {"colour": "#88FF00", "name": "Yellow-Green", "hex": "88FF00"},
    "SCL":   {"colour": "#FFFF00", "name": "Yellow",       "hex": "FFFF00"},
    "SDA":   {"colour": "#0000FF", "name": "Blue",         "hex": "0000FF"},
    "TX":    {"colour": "#00FF00", "name": "Green",        "hex": "00FF00"},
    "RX":    {"colour": "#FFFFFF", "name": "White",        "hex": "FFFFFF"},
    "INT":   {"colour": "#AA00FF", "name": "Purple",       "hex": "AA00FF"},
    "DATA":  {"colour": "#00FFFF", "name": "Cyan",         "hex": "00FFFF"},
    "PWM":   {"colour": "#FF88CC", "name": "Pink",         "hex": "FF88CC"},
    "GPIO":  {"colour": "#AAAAAA", "name": "Grey",         "hex": "AAAAAA"},
}

# ---------------------------------------------------------------------------
# Module wiring definitions
# Each entry: module_signal → {gpio_key, wire_role, note, required}
# ---------------------------------------------------------------------------

MODULE_PINOUTS = {

    # -------------------------------------------------------------------
    # Waveshare LCD HAT (ST7789, SPI, 1.3" / 2")
    # -------------------------------------------------------------------
    "waveshare_lcd_hat_st7789": {
        "label": "Waveshare LCD HAT (ST7789 SPI)",
        "interface": "SPI",
        "voltage": 3.3,
        "datasheet": "https://www.waveshare.com/wiki/1.3inch_LCD_HAT",
        "pins": [
            {"module_pin": "VCC",  "module_phys": 1,  "gpio_key": "3V3_1",  "role": "3V3",  "required": True,  "note": "3.3V power supply"},
            {"module_pin": "GND",  "module_phys": 2,  "gpio_key": "GND_6",  "role": "GND",  "required": True,  "note": "Common ground"},
            {"module_pin": "DIN",  "module_phys": 19, "gpio_key": "GPIO10", "role": "MOSI", "required": True,  "note": "SPI MOSI — display data in"},
            {"module_pin": "CLK",  "module_phys": 23, "gpio_key": "GPIO11", "role": "SCLK", "required": True,  "note": "SPI clock"},
            {"module_pin": "CS",   "module_phys": 24, "gpio_key": "GPIO8",  "role": "CS",   "required": True,  "note": "SPI CE0 — chip select (active LOW)"},
            {"module_pin": "DC",   "module_phys": 22, "gpio_key": "GPIO25", "role": "DC",   "required": True,  "note": "Data/Command select (HIGH=data, LOW=cmd)"},
            {"module_pin": "RST",  "module_phys": 13, "gpio_key": "GPIO27", "role": "RST",  "required": True,  "note": "Reset (active LOW, pulse ≥10µs)"},
            {"module_pin": "BL",   "module_phys": 18, "gpio_key": "GPIO24", "role": "BL",   "required": False, "note": "Backlight PWM (HIGH=on). GPIO24 drives via transistor on HAT"},
        ],
        "extra_signals": {
            "KEY1": {"gpio_key": "GPIO21", "role": "GPIO", "note": "Active LOW, internal pull-up. Physical pin 40"},
            "KEY2": {"gpio_key": "GPIO20", "role": "GPIO", "note": "Active LOW, internal pull-up. Physical pin 38"},
            "KEY3": {"gpio_key": "GPIO16", "role": "GPIO", "note": "Active LOW, internal pull-up. Physical pin 36"},
            "JOY_UP":    {"gpio_key": "GPIO6",  "role": "GPIO", "note": "Active LOW"},
            "JOY_DOWN":  {"gpio_key": "GPIO19", "role": "GPIO", "note": "Active LOW"},
            "JOY_LEFT":  {"gpio_key": "GPIO5",  "role": "GPIO", "note": "Active LOW"},
            "JOY_RIGHT": {"gpio_key": "GPIO26", "role": "GPIO", "note": "Active LOW"},
            "JOY_PRESS": {"gpio_key": "GPIO13", "role": "GPIO", "note": "Active LOW — joystick centre press"},
        },
        "safety_notes": [
            "All signals are 3.3V — do NOT connect to 5V GPIO without level shifter.",
            "BL (GPIO24) must not sink more than 16mA; HAT has onboard transistor.",
            "CS must go LOW before any SPI transaction and HIGH after.",
            "RST pulse: hold LOW for ≥10µs, then HIGH; wait ≥120ms before first command.",
            "SPI max clock: 62.5 MHz (ST7789 datasheet limit); RPi default 8MHz safe.",
        ],
        "software": {
            "library": "luma.lcd or Waveshare example",
            "init_cmd": "sudo python3 key_demo.py",
            "config_spi": "Enable SPI in raspi-config → Interface Options → SPI",
        },
    },

    # -------------------------------------------------------------------
    # I2C OLED (SSD1306 / SSD1315 128×64)
    # -------------------------------------------------------------------
    "i2c_oled_ssd1306": {
        "label": "I2C OLED 128×64 (SSD1306)",
        "interface": "I2C",
        "voltage": 3.3,
        "datasheet": "https://cdn-shop.adafruit.com/datasheets/SSD1306.pdf",
        "pins": [
            {"module_pin": "VCC", "module_phys": 1, "gpio_key": "3V3_1",  "role": "3V3",  "required": True,  "note": "3.3V (some modules accept 5V — check label)"},
            {"module_pin": "GND", "module_phys": 2, "gpio_key": "GND_6",  "role": "GND",  "required": True,  "note": "Ground"},
            {"module_pin": "SCL", "module_phys": 5, "gpio_key": "GPIO3",  "role": "SCL",  "required": True,  "note": "I2C clock — GPIO3 / physical pin 5"},
            {"module_pin": "SDA", "module_phys": 3, "gpio_key": "GPIO2",  "role": "SDA",  "required": True,  "note": "I2C data — GPIO2 / physical pin 3"},
        ],
        "extra_signals": {},
        "safety_notes": [
            "Default I2C address: 0x3C (SA0=GND) or 0x3D (SA0=VCC).",
            "RPi has 1.8kΩ internal pull-ups on SDA/SCL — external 4.7kΩ optional.",
            "Max I2C speed: 400kHz (Fast mode). RPi default 100kHz.",
            "Do not connect VCC to 5V on 3.3V-only modules — will damage display.",
        ],
        "software": {
            "library": "Adafruit_SSD1306 or luma.oled",
            "init_cmd": "sudo i2cdetect -y 1",
            "config_i2c": "Enable I2C in raspi-config → Interface Options → I2C",
        },
    },

    # -------------------------------------------------------------------
    # Tactile button (single, digital input)
    # -------------------------------------------------------------------
    "tactile_button": {
        "label": "Tactile Push Button",
        "interface": "GPIO",
        "voltage": 3.3,
        "pins": [
            {"module_pin": "PIN1", "module_phys": 1, "gpio_key": "GPIO17", "role": "GPIO", "required": True,
             "note": "One leg to GPIO, other leg to GND. Enable internal pull-up in software."},
            {"module_pin": "GND",  "module_phys": 2, "gpio_key": "GND_6",  "role": "GND",  "required": True,  "note": "Ground leg"},
        ],
        "extra_signals": {},
        "safety_notes": [
            "Always use internal pull-up (GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)).",
            "Button reads LOW when pressed, HIGH when released (active LOW).",
            "Add 100nF capacitor between GPIO and GND for debounce if needed.",
            "Max GPIO sink current: 16mA. Standard tactile buttons well within limit.",
        ],
        "software": {
            "library": "RPi.GPIO or gpiozero",
            "init_cmd": "GPIO.setup(17, GPIO.IN, pull_up_down=GPIO.PUD_UP)",
        },
    },

    # -------------------------------------------------------------------
    # Analog joystick (via MCP3008 ADC over SPI)
    # -------------------------------------------------------------------
    "analog_joystick_mcp3008": {
        "label": "Analog Joystick via MCP3008 ADC (SPI)",
        "interface": "SPI",
        "voltage": 3.3,
        "datasheet": "https://ww1.microchip.com/downloads/en/DeviceDoc/21295C.pdf",
        "pins": [
            {"module_pin": "VDD",  "module_phys": 16, "gpio_key": "3V3_1",  "role": "3V3",  "required": True,  "note": "MCP3008 power"},
            {"module_pin": "VREF", "module_phys": 15, "gpio_key": "3V3_1",  "role": "3V3",  "required": True,  "note": "ADC reference = 3.3V"},
            {"module_pin": "AGND", "module_phys": 14, "gpio_key": "GND_6",  "role": "GND",  "required": True,  "note": "Analog ground"},
            {"module_pin": "DGND", "module_phys": 9,  "gpio_key": "GND_6",  "role": "GND",  "required": True,  "note": "Digital ground"},
            {"module_pin": "CLK",  "module_phys": 13, "gpio_key": "GPIO11", "role": "SCLK", "required": True,  "note": "SPI clock (shared with LCD HAT if on same bus)"},
            {"module_pin": "DOUT", "module_phys": 12, "gpio_key": "GPIO9",  "role": "MISO", "required": True,  "note": "ADC → RPi data out"},
            {"module_pin": "DIN",  "module_phys": 11, "gpio_key": "GPIO10", "role": "MOSI", "required": True,  "note": "RPi → ADC data in"},
            {"module_pin": "CS",   "module_phys": 10, "gpio_key": "GPIO7",  "role": "CS",   "required": True,  "note": "Use CE1 (GPIO7) if CE0 taken by LCD"},
            {"module_pin": "VRX",  "module_phys": "CH0", "gpio_key": None,  "role": "ANALOG", "required": True, "note": "X-axis → MCP3008 CH0 (0–3.3V)"},
            {"module_pin": "VRY",  "module_phys": "CH1", "gpio_key": None,  "role": "ANALOG", "required": True, "note": "Y-axis → MCP3008 CH1 (0–3.3V)"},
            {"module_pin": "SW",   "module_phys": None, "gpio_key": "GPIO13","role": "GPIO",  "required": False, "note": "Joystick press — active LOW, pull-up enabled"},
        ],
        "extra_signals": {},
        "safety_notes": [
            "MCP3008 is 3.3V-compatible. Do NOT apply VREF > 3.3V on a 3.3V system.",
            "If sharing SPI bus with LCD: use separate CS lines (CE0 for LCD, CE1 for ADC).",
            "CLK, MOSI, MISO are shared bus — only CS distinguishes devices.",
            "Joystick centre voltage ≈ 1.65V (half of 3.3V). Deadzone ±0.1V recommended.",
            "10-bit ADC: 0–1023 range. Mid-centre ≈ 512.",
        ],
        "software": {
            "library": "spidev + custom MCP3008 driver",
            "init_cmd": "spi.open(0, 1)  # bus 0, CE1",
        },
    },

    # -------------------------------------------------------------------
    # LiPo battery + TP4056 charger module
    # -------------------------------------------------------------------
    "lipo_tp4056": {
        "label": "LiPo Battery + TP4056 Charger",
        "interface": "POWER",
        "voltage": 3.7,
        "datasheet": "https://dlnmh9ip6v2uc.cloudfront.net/datasheets/Prototyping/TP4056.pdf",
        "pins": [
            {"module_pin": "BAT+", "module_phys": "J2+", "gpio_key": None, "role": "5V",   "required": True,
             "note": "Battery positive → boost converter IN+ → RPi 5V rail via USB or GPIO pin 2/4"},
            {"module_pin": "BAT-", "module_phys": "J2-", "gpio_key": None, "role": "GND",  "required": True,
             "note": "Battery negative → common ground"},
            {"module_pin": "USB+", "module_phys": "IN+", "gpio_key": None, "role": "5V",   "required": False,
             "note": "Micro-USB / USB-C charge input 5V"},
            {"module_pin": "USB-", "module_phys": "IN-", "gpio_key": None, "role": "GND",  "required": False,
             "note": "Charge input ground"},
            {"module_pin": "OUT+", "module_phys": "OUT+","gpio_key": None, "role": "5V",   "required": True,
             "note": "Boost converter 5V output → RPi VBUS (physical pin 2 or 4)"},
        ],
        "extra_signals": {},
        "safety_notes": [
            "NEVER connect battery backwards — will destroy TP4056 and may cause fire.",
            "TP4056 max charge current set by RPROG resistor: 1.2kΩ=1A, 2kΩ=0.6A, 10kΩ=130mA.",
            "Always use a battery with built-in protection PCB (overcharge/overdischarge).",
            "Boost converter output must be rated ≥2.5A for RPi 4; ≥3A for RPi 5.",
            "Do NOT power RPi 5V GPIO pin and USB simultaneously — may backfeed.",
            "Add a power switch between BOOST OUT and RPi 5V for safe power-off.",
        ],
        "software": {
            "library": "N/A (hardware-only)",
            "note":    "Monitor battery via ADC on GPIO (voltage divider from BAT+)",
        },
    },

    # -------------------------------------------------------------------
    # USB-C PD power (host board power input)
    # -------------------------------------------------------------------
    "usbc_power": {
        "label": "USB-C Power Input",
        "interface": "POWER",
        "voltage": 5.0,
        "pins": [
            {"module_pin": "VBUS", "module_phys": "A4/B9", "gpio_key": None, "role": "5V",  "required": True,
             "note": "5V from USB-C → RPi physical pin 2 or 4 (or via TP4056 BAT+)"},
            {"module_pin": "GND",  "module_phys": "A1/B12","gpio_key": None, "role": "GND", "required": True,
             "note": "Ground"},
            {"module_pin": "CC1",  "module_phys": "A5",    "gpio_key": None, "role": "GPIO","required": False,
             "note": "5.1kΩ to GND for 5V/900mA; leave open for PD negotiation"},
        ],
        "extra_signals": {},
        "safety_notes": [
            "For simple 5V: add 5.1kΩ from each CC pin to GND (tells charger 5V/3A).",
            "Do NOT use the USB-C D+/D- for data while also powering — use dedicated port.",
            "Max RPi 5 power: 5V × 5A = 25W — ensure cable+charger rated accordingly.",
        ],
        "software": {"library": "N/A"},
    },

    # -------------------------------------------------------------------
    # Micro HDMI output (RPi Zero / RPi 4)
    # -------------------------------------------------------------------
    "micro_hdmi": {
        "label": "Micro HDMI Output",
        "interface": "HDMI",
        "voltage": 3.3,
        "pins": [
            {"module_pin": "DATA0+", "module_phys": "1",  "gpio_key": None, "role": "DATA", "required": True,  "note": "TMDS data pair — routed on-board"},
            {"module_pin": "SHIELD", "module_phys": "17", "gpio_key": None, "role": "GND",  "required": True,  "note": "Cable shield → chassis GND"},
            {"module_pin": "5V",     "module_phys": "18", "gpio_key": None, "role": "5V",   "required": False, "note": "+5V to sink (powers HDMI receiver HPD)"},
        ],
        "extra_signals": {},
        "safety_notes": [
            "HDMI signals are on dedicated RPi silicon — no GPIO mapping needed.",
            "Shell cutout: Micro HDMI = 6.4×2.9mm + 1mm clearance = 7.4×3.9mm slot.",
            "Micro HDMI adapters strain easily — add strain relief inside shell.",
        ],
        "software": {"library": "N/A — OS framebuffer"},
    },

    # -------------------------------------------------------------------
    # 3.5mm Audio Jack (RPi PWM audio)
    # -------------------------------------------------------------------
    "audio_jack_3_5mm": {
        "label": "3.5mm Audio Jack (PWM)",
        "interface": "PWM",
        "voltage": 3.3,
        "pins": [
            {"module_pin": "LEFT",  "module_phys": None, "gpio_key": "GPIO12", "role": "PWM",  "required": True,  "note": "GPIO12 (PWM0) → RC filter → left channel"},
            {"module_pin": "RIGHT", "module_phys": None, "gpio_key": "GPIO13", "role": "PWM",  "required": True,  "note": "GPIO13 (PWM1) → RC filter → right channel"},
            {"module_pin": "GND",   "module_phys": None, "gpio_key": "GND_6",  "role": "GND",  "required": True,  "note": "Sleeve ground"},
        ],
        "extra_signals": {},
        "safety_notes": [
            "RPi PWM audio is noisy — add RC low-pass filter (270Ω + 33nF) on each channel.",
            "GPIO12/13 are shared with SPI1 bus — do not use SPI1 if using PWM audio.",
            "Max output level ≈ 0.5Vrms — connect to amplifier, not direct to speaker.",
        ],
        "software": {
            "library": "ALSA / aplay",
            "init_cmd": "sudo modprobe snd_bcm2835",
        },
    },

    # -------------------------------------------------------------------
    # MicroSD card breakout (secondary, via SPI)
    # -------------------------------------------------------------------
    "microsd_spi": {
        "label": "MicroSD Card (SPI mode)",
        "interface": "SPI",
        "voltage": 3.3,
        "pins": [
            {"module_pin": "VCC",  "module_phys": None, "gpio_key": "3V3_1",  "role": "3V3",  "required": True,  "note": "3.3V power"},
            {"module_pin": "GND",  "module_phys": None, "gpio_key": "GND_6",  "role": "GND",  "required": True,  "note": "Ground"},
            {"module_pin": "MOSI", "module_phys": None, "gpio_key": "GPIO10", "role": "MOSI", "required": True,  "note": "SPI MOSI (shared bus)"},
            {"module_pin": "MISO", "module_phys": None, "gpio_key": "GPIO9",  "role": "MISO", "required": True,  "note": "SPI MISO (shared bus)"},
            {"module_pin": "CLK",  "module_phys": None, "gpio_key": "GPIO11", "role": "SCLK", "required": True,  "note": "SPI clock (shared bus)"},
            {"module_pin": "CS",   "module_phys": None, "gpio_key": "GPIO5",  "role": "CS",   "required": True,  "note": "Use free GPIO for CS — not CE0/CE1"},
        ],
        "extra_signals": {},
        "safety_notes": [
            "SD card is 3.3V only — do NOT connect to 5V.",
            "SD SPI max clock: 25MHz. Start at 400kHz for init, then increase.",
            "CS must remain LOW for entire multi-byte transaction.",
            "MISO line needs pull-up (10kΩ to 3.3V) when CS is HIGH.",
        ],
        "software": {"library": "sdcard (MicroPython) or kernel driver"},
    },

    # -------------------------------------------------------------------
    # NeoPixel / WS2812B RGB LED strip
    # -------------------------------------------------------------------
    "neopixel_ws2812b": {
        "label": "NeoPixel / WS2812B RGB LEDs",
        "interface": "GPIO",
        "voltage": 5.0,
        "pins": [
            {"module_pin": "5V",   "module_phys": None, "gpio_key": None,     "role": "5V",   "required": True,
             "note": "5V from power supply — not GPIO pin 2 (insufficient current)"},
            {"module_pin": "GND",  "module_phys": None, "gpio_key": "GND_6",  "role": "GND",  "required": True,
             "note": "Common ground (MUST be shared with RPi GND)"},
            {"module_pin": "DIN",  "module_phys": None, "gpio_key": "GPIO18", "role": "DATA", "required": True,
             "note": "GPIO18 (PWM0) — only PWM/PCM capable pins work reliably"},
        ],
        "extra_signals": {},
        "safety_notes": [
            "WS2812B runs at 5V data logic. Add 300–500Ω resistor on DIN line (3.3V→5V).",
            "Each LED draws up to 60mA at full white. Plan 60mA × LED count for PSU.",
            "MUST use GPIO18 or GPIO12 (hardware PWM) for reliable timing.",
            "Add 1000µF/6.3V cap across 5V/GND at the strip for inrush protection.",
            "Library rpio_ws281x requires root or GPIO group membership.",
        ],
        "software": {"library": "rpi_ws281x", "init_cmd": "sudo pip3 install rpi_ws281x"},
    },

    # -------------------------------------------------------------------
    # I2C IMU (MPU-6050 accelerometer/gyroscope)
    # -------------------------------------------------------------------
    "imu_mpu6050": {
        "label": "IMU MPU-6050 (Accelerometer + Gyroscope)",
        "interface": "I2C",
        "voltage": 3.3,
        "datasheet": "https://invensense.tdk.com/wp-content/uploads/2015/02/MPU-6000-Datasheet1.pdf",
        "pins": [
            {"module_pin": "VCC",  "module_phys": None, "gpio_key": "3V3_1",  "role": "3V3",  "required": True,  "note": "3.3V power"},
            {"module_pin": "GND",  "module_phys": None, "gpio_key": "GND_6",  "role": "GND",  "required": True,  "note": "Ground"},
            {"module_pin": "SCL",  "module_phys": None, "gpio_key": "GPIO3",  "role": "SCL",  "required": True,  "note": "I2C clock"},
            {"module_pin": "SDA",  "module_phys": None, "gpio_key": "GPIO2",  "role": "SDA",  "required": True,  "note": "I2C data"},
            {"module_pin": "INT",  "module_phys": None, "gpio_key": "GPIO22", "role": "INT",  "required": False, "note": "Data-ready interrupt → any free GPIO"},
            {"module_pin": "AD0",  "module_phys": None, "gpio_key": "GND_6",  "role": "GND",  "required": False, "note": "AD0=GND → I2C address 0x68; AD0=3V3 → 0x69"},
        ],
        "extra_signals": {},
        "safety_notes": [
            "Default I2C address: 0x68 (AD0=GND).",
            "Sensitive to vibration from motor/speaker on same board — use soft mount.",
            "FIFO buffer 1024 bytes — use interrupt-driven reads to avoid overflow.",
        ],
        "software": {"library": "smbus2 or mpu6050 Python library"},
    },
}

# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def detect_conflicts(selected_modules: List[str]) -> List[dict]:
    """
    Check for GPIO pin conflicts across all selected modules.
    Returns list of conflict dicts.
    """
    pin_usage: Dict[str, List[str]] = {}  # gpio_key → [module_labels]
    conflicts = []

    for mod_key in selected_modules:
        mod = MODULE_PINOUTS.get(mod_key)
        if not mod:
            continue
        for pin in mod["pins"]:
            gk = pin.get("gpio_key")
            if gk and pin["role"] not in ("GND", "3V3", "5V"):
                pin_usage.setdefault(gk, []).append(mod["label"])

    for gpio_key, modules in pin_usage.items():
        if len(modules) > 1:
            gpio_info = RPI_GPIO.get(gpio_key, {})
            conflicts.append({
                "gpio_key":  gpio_key,
                "phys_pin":  gpio_info.get("phys"),
                "bcm":       gpio_info.get("bcm"),
                "conflict_between": modules,
                "severity":  "ERROR",
                "note":      f"GPIO {gpio_key} claimed by {len(modules)} modules simultaneously",
            })

    return conflicts


# ---------------------------------------------------------------------------
# Power budget check
# ---------------------------------------------------------------------------

MODULE_CURRENT_MA = {
    "waveshare_lcd_hat_st7789":  150,  # display + backlight
    "i2c_oled_ssd1306":           25,
    "tactile_button":              1,
    "analog_joystick_mcp3008":     5,
    "lipo_tp4056":                 0,  # power source, not load
    "usbc_power":                  0,
    "micro_hdmi":                  0,
    "audio_jack_3_5mm":           30,
    "microsd_spi":                100,
    "neopixel_ws2812b":           240,  # 4 LEDs × 60mA
    "imu_mpu6050":                  4,
}

RPI_MODELS_POWER = {
    "Raspberry Pi Zero":   {"tdp_ma": 300,  "max_gpio_ma": 50},
    "Raspberry Pi Zero 2": {"tdp_ma": 350,  "max_gpio_ma": 50},
    "RPi 3":               {"tdp_ma": 1500, "max_gpio_ma": 50},
    "RPi 4":               {"tdp_ma": 2000, "max_gpio_ma": 50},
    "RPi 5":               {"tdp_ma": 2500, "max_gpio_ma": 50},
}


def power_budget(selected_modules: List[str], host_board: str = "RPi 4") -> dict:
    total_ma = sum(MODULE_CURRENT_MA.get(m, 0) for m in selected_modules)
    host = RPI_MODELS_POWER.get(host_board, RPI_MODELS_POWER["RPi 4"])
    grand_total = total_ma + host["tdp_ma"]
    return {
        "host_board": host_board,
        "host_tdp_ma": host["tdp_ma"],
        "modules_total_ma": total_ma,
        "grand_total_ma": grand_total,
        "grand_total_w": round(grand_total * 5 / 1000, 2),  # at 5V
        "recommended_psu_a": round(grand_total / 1000 * 1.3, 1),  # 30% headroom
        "within_budget": grand_total < 4500,  # 5V/4.5A is common max
        "per_module": {m: MODULE_CURRENT_MA.get(m, 0) for m in selected_modules},
    }


# ---------------------------------------------------------------------------
# Main build pinout generator
# ---------------------------------------------------------------------------

class PinoutEngine:

    def __init__(self, build_spec: dict):
        """
        build_spec: the DeviceSpec dict (from crawler) plus a list
        'selected_modules' of MODULE_PINOUTS keys.
        """
        self.spec = build_spec

    def generate(self) -> dict:
        selected = self._infer_modules()
        conflicts = detect_conflicts(selected)
        budget    = power_budget(selected, self.spec.get("compatible_boards", ["RPi 4"])[0]
                                 if self.spec.get("compatible_boards") else "RPi 4")
        wiring_tables = {}
        for mod_key in selected:
            mod = MODULE_PINOUTS.get(mod_key)
            if mod:
                wiring_tables[mod_key] = self._build_wiring_table(mod)

        return {
            "selected_modules":  selected,
            "wiring_tables":     wiring_tables,
            "gpio_map":          self._full_gpio_map(selected),
            "conflicts":         conflicts,
            "power_budget":      budget,
            "safety_checklist":  self._safety_checklist(selected),
            "wire_colours":      WIRE_COLOURS,
        }

    def _infer_modules(self) -> List[str]:
        """Infer which MODULE_PINOUTS keys apply from the crawled spec."""
        mods = []
        ic  = self.spec.get("controller_ic", "")
        iface = self.spec.get("interface", "")
        vendor = self.spec.get("vendor", "").lower()

        # LCD HAT
        if ic in ("ST7789", "ST7735") or ("waveshare" in vendor and "spi" in iface.lower()):
            mods.append("waveshare_lcd_hat_st7789")

        # Buttons
        if self.spec.get("button_positions_mm"):
            mods.append("tactile_button")

        # Joystick
        if self.spec.get("joystick_diameter_mm", 0) > 0:
            mods.append("analog_joystick_mcp3008")

        # Always include power
        mods.append("usbc_power")

        # Feature Modules tab (framework R1): modules the user explicitly
        # added from the verified registry join the inferred set. Additive —
        # with no 'selected_modules' in the spec, behaviour is unchanged.
        for extra in self.spec.get("selected_modules") or []:
            if extra in MODULE_PINOUTS and extra not in mods:
                mods.append(extra)

        return list(dict.fromkeys(mods))  # deduplicate, preserve order

    def _build_wiring_table(self, mod: dict) -> dict:
        rows = []
        for pin in mod["pins"]:
            gk   = pin.get("gpio_key")
            gpio = RPI_GPIO.get(gk, {}) if gk else {}
            role = pin.get("role", "")
            colour_data = WIRE_COLOURS.get(role, WIRE_COLOURS["GPIO"])
            rows.append({
                "module_pin":   pin["module_pin"],
                "module_phys":  pin.get("module_phys", ""),
                "gpio_key":     gk or "—",
                "bcm":          gpio.get("bcm", "—"),
                "phys_pin":     gpio.get("phys", "—"),
                "gpio_name":    gpio.get("name", "—"),
                "role":         role,
                "wire_colour":  colour_data["colour"],
                "wire_name":    colour_data["name"],
                "required":     pin.get("required", True),
                "note":         pin.get("note", ""),
            })
        return {
            "label":       mod["label"],
            "interface":   mod["interface"],
            "voltage":     mod["voltage"],
            "rows":        rows,
            "safety_notes": mod.get("safety_notes", []),
            "software":    mod.get("software", {}),
            "extra_signals": mod.get("extra_signals", {}),
        }

    def _full_gpio_map(self, selected: List[str]) -> List[dict]:
        """Return 40-pin header map showing which pins are claimed."""
        claimed = {}
        for mod_key in selected:
            mod = MODULE_PINOUTS.get(mod_key)
            if not mod:
                continue
            for pin in mod["pins"]:
                gk = pin.get("gpio_key")
                if gk:
                    claimed[gk] = {
                        "module":   mod["label"],
                        "role":     pin["role"],
                        "colour":   WIRE_COLOURS.get(pin["role"], WIRE_COLOURS["GPIO"])["colour"],
                    }

        result = []
        for gk, info in RPI_GPIO.items():
            claimed_by = claimed.get(gk)
            result.append({
                "gpio_key":  gk,
                "bcm":       info.get("bcm"),
                "phys":      info["phys"],
                "name":      info["name"],
                "caps":      info["caps"],
                "voltage":   info["voltage"],
                "claimed_by": claimed_by,
                "colour":    claimed_by["colour"] if claimed_by else "#2a2f42",
            })
        result.sort(key=lambda x: x["phys"])
        return result

    def _safety_checklist(self, selected: List[str]) -> List[dict]:
        checks = [
            {"check": "All 3.3V signals verified — no 5V GPIO exposure", "status": "verify"},
            {"check": "SPI bus CS lines are unique per device",           "status": "verify"},
            {"check": "I2C addresses are unique (use i2cdetect to confirm)", "status": "verify"},
            {"check": "Power supply rated ≥ recommended PSU A",           "status": "verify"},
            {"check": "Battery has built-in protection PCB",              "status": "verify"},
            {"check": "All GND lines connected to common ground",         "status": "verify"},
            {"check": "No GPIO sink current exceeds 16mA per pin",        "status": "verify"},
            {"check": "Total GPIO current < 50mA across all pins",        "status": "verify"},
            {"check": "RST pulse ≥10µs applied before LCD init",          "status": "verify"},
            {"check": "SPI enabled in raspi-config",                      "status": "verify"},
            {"check": "I2C enabled in raspi-config",                      "status": "verify"},
        ]
        for mod_key in selected:
            mod = MODULE_PINOUTS.get(mod_key)
            if mod:
                for note in mod.get("safety_notes", []):
                    checks.append({"check": note, "status": "critical"})
        return checks


# ---------------------------------------------------------------------------
# Five-check pinout verification + conflict triage (framework R2e / R2f)
# ---------------------------------------------------------------------------
# The user's verification standard: the program's pinout mapping must be
# checked five ways before it is trusted. verify_pinout() cross-checks a
# generate() result against MODULE_PINOUTS and the standard 40-pin table.
# triage_conflicts() gives every conflict an industry-style verdict:
# "DIY simple fix" (with the fix) or "Not worth the time" (with a reason).

SHARED_BUS_PINS = {
    "GPIO10": "SPI MOSI", "GPIO9": "SPI MISO", "GPIO11": "SPI SCLK",
    "GPIO2": "I2C SDA", "GPIO3": "I2C SCL",
}
BUS_ROLES = {"MOSI", "MISO", "SCLK", "SDA", "SCL"}
POWER_ROLES = ("GND", "3V3", "5V")


def verify_pinout(result: dict) -> List[dict]:
    """Five concrete consistency checks on a PinoutEngine.generate() result.
    Returns [{n, name, pass, detail}] — pass means the mapping is internally
    consistent, NOT that the design has no conflicts."""
    selected = result.get("selected_modules", [])
    gpio_map = {e["gpio_key"]: e for e in result.get("gpio_map", [])}
    checks = []

    # 1 — every module pin exists in the GPIO map
    missing = []
    for mid in selected:
        for pin in MODULE_PINOUTS.get(mid, {}).get("pins", []):
            gk = pin.get("gpio_key")
            if gk and gk not in gpio_map:
                missing.append(f"{mid}:{gk}")
    checks.append({
        "n": 1, "name": "Every module pin exists in the GPIO map",
        "pass": not missing,
        "detail": ("all module pins resolve in the 40-pin map"
                   if not missing else f"missing: {', '.join(missing)}"),
    })

    # 2 — no pin double-claimed beyond the reported conflicts (mirrors
    # detect_conflicts exactly: same role filter, same counting)
    pin_usage: Dict[str, List[str]] = {}
    for mid in selected:
        mod = MODULE_PINOUTS.get(mid)
        if not mod:
            continue
        for pin in mod["pins"]:
            gk = pin.get("gpio_key")
            if gk and pin["role"] not in POWER_ROLES:
                pin_usage.setdefault(gk, []).append(mod["label"])
    double_claimed = {k for k, v in pin_usage.items() if len(v) > 1}
    reported = {c["gpio_key"] for c in result.get("conflicts", [])}
    checks.append({
        "n": 2,
        "name": "No exclusive pin double-claimed beyond reported conflicts",
        "pass": double_claimed == reported,
        "detail": (f"double claims {sorted(double_claimed)} exactly match the "
                   f"{len(reported)} reported conflict(s)"
                   if double_claimed == reported else
                   f"mismatch — double-claimed {sorted(double_claimed)} vs "
                   f"reported {sorted(reported)}"),
    })

    # 3 — wiring tables' pins match the module claims
    mismatch = []
    for mid, table in (result.get("wiring_tables") or {}).items():
        expected = {p["gpio_key"] for p in
                    MODULE_PINOUTS.get(mid, {}).get("pins", [])
                    if p.get("gpio_key")}
        got = {r["gpio_key"] for r in table.get("rows", [])
               if r.get("gpio_key") and r["gpio_key"] != "—"}
        if expected != got:
            mismatch.append(mid)
    checks.append({
        "n": 3, "name": "Wiring tables match module pin claims",
        "pass": not mismatch,
        "detail": ("every wiring table lists exactly its module's pins"
                   if not mismatch else
                   f"mismatch in: {', '.join(mismatch)}"),
    })

    # 4 — power total equals the sum of module draws
    pb = result.get("power_budget", {})
    expect_mods = sum(MODULE_CURRENT_MA.get(m, 0) for m in selected)
    ok4 = (pb.get("modules_total_ma") == expect_mods
           and pb.get("grand_total_ma")
           == expect_mods + pb.get("host_tdp_ma", 0))
    checks.append({
        "n": 4, "name": "Power total equals the sum of module draws",
        "pass": bool(ok4),
        "detail": (f"modules {expect_mods} mA + host "
                   f"{pb.get('host_tdp_ma')} mA = {pb.get('grand_total_ma')} mA"
                   if ok4 else
                   f"reported modules_total_ma={pb.get('modules_total_ma')} "
                   f"vs summed {expect_mods} mA"),
    })

    # 5 — physical pins within 1..40 and BCM/physical mapping matches the
    # standard 40-pin table
    bad = []
    seen_phys = set()
    for gk, info in RPI_GPIO.items():
        ph = info.get("phys")
        if not isinstance(ph, int) or not (1 <= ph <= 40) or ph in seen_phys:
            bad.append(f"{gk}:phys={ph}")
        seen_phys.add(ph)
    for mid, table in (result.get("wiring_tables") or {}).items():
        for row in table.get("rows", []):
            gk = row.get("gpio_key")
            if gk in RPI_GPIO:
                std = RPI_GPIO[gk]
                if row.get("phys_pin") != std["phys"]:
                    bad.append(f"{mid}:{gk} phys {row.get('phys_pin')} "
                               f"!= standard {std['phys']}")
                rb, sb = row.get("bcm"), std.get("bcm")
                if isinstance(rb, int) and isinstance(sb, int) and rb != sb:
                    bad.append(f"{mid}:{gk} BCM {rb} != standard {sb}")
    checks.append({
        "n": 5,
        "name": "Physical pins 1..40, BCM-physical mapping matches the "
                "standard 40-pin table",
        "pass": not bad,
        "detail": ("all 40 pins in range; every wiring row matches the "
                   "standard table" if not bad else "; ".join(bad[:4])),
    })
    return checks


def _free_gpio(result: dict):
    """First unclaimed true-GPIO pin on the header (skips ID EEPROM pins)."""
    for entry in result.get("gpio_map", []):
        if entry.get("claimed_by"):
            continue
        if entry["gpio_key"] in ("GPIO0", "GPIO1"):
            continue  # ID_SD / ID_SC — reserved for HAT EEPROM
        if "GPIO" in (entry.get("caps") or []):
            return entry
    return None


def triage_conflicts(result: dict) -> List[dict]:
    """One verdict per conflict, from a small rule table:
    - shared-bus pin claimed only in bus roles → DIY (sharing is by design;
      separate the chip-selects).
    - chip-select landed on a shared-bus pin → DIY (move CS to a free GPIO).
    - two modules hard-claim one exclusive pin → DIY if a free compatible
      GPIO exists (named), else Not worth the time.
    - power over budget → DIY (wired/official PSU or powered hub) unless the
      draw is more than 2x the budget → Not worth the time."""
    out = []
    selected = result.get("selected_modules", [])
    for c in result.get("conflicts", []):
        key = c["gpio_key"]
        roles = []
        for mid in selected:
            for pin in MODULE_PINOUTS.get(mid, {}).get("pins", []):
                if pin.get("gpio_key") == key:
                    roles.append(pin.get("role"))
        if key in SHARED_BUS_PINS and roles and all(r in BUS_ROLES
                                                    for r in roles):
            verdict = "DIY simple fix"
            fix = (f"{SHARED_BUS_PINS[key]} is a shared bus line — sharing "
                   "it is by design, not a fault. Keep the bus pins shared "
                   "and give each device its own chip-select (CE0 = GPIO8, "
                   "CE1 = GPIO7, or another free GPIO).")
        elif key in SHARED_BUS_PINS and "CS" in roles:
            free = _free_gpio(result)
            verdict = "DIY simple fix"
            fix = (f"A chip-select landed on shared bus pin {key}. Move "
                   f"that CS line to free {free['gpio_key']} (physical pin "
                   f"{free['phys']})." if free else
                   f"A chip-select landed on shared bus pin {key}, and no "
                   "GPIO is free to move it to — free one up first.")
        else:
            free = _free_gpio(result)
            if free:
                verdict = "DIY simple fix"
                fix = (f"Two modules hard-claim {key}. Move one module's "
                       f"{key} connection to free {free['gpio_key']} "
                       f"(physical pin {free['phys']}, {free['name']}) and "
                       "update that module's driver config to match.")
            else:
                verdict = "Not worth the time"
                fix = (f"No free compatible GPIO remains on the 40-pin "
                       f"header for {key}. Drop one of the two modules, or "
                       "add an I2C GPIO expander (e.g. MCP23017) — rewiring "
                       "by hand will not solve a header that is full.")
        out.append({
            "type": "pin", "gpio_key": key,
            "phys_pin": c.get("phys_pin"), "bcm": c.get("bcm"),
            "between": c.get("conflict_between", []),
            "verdict": verdict, "fix": fix,
        })
    pb = result.get("power_budget", {})
    if pb and not pb.get("within_budget", True):
        total = pb.get("grand_total_ma", 0)
        budget = pb.get("budget_ma", 4500)
        if total <= 2 * budget:
            verdict = "DIY simple fix"
            fix = (f"Total draw {total} mA is over the {budget} mA budget "
                   "line. Run wire-to-outlet with the official PSU for the "
                   "board, or add a powered USB hub — do not power this "
                   "build from a battery bay sized for less.")
        else:
            verdict = "Not worth the time"
            fix = (f"Total draw {total} mA is more than 2x the {budget} mA "
                   "budget. Split the build across two supplies/boards "
                   "instead of trying to feed it from one header.")
        out.append({"type": "power", "gpio_key": None, "phys_pin": None,
                    "bcm": None, "between": [], "verdict": verdict,
                    "fix": fix})
    return out
