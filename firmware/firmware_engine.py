"""
firmware_engine.py
------------------
Firmware architecture engine for the handheld device builder.

Responsibilities:
  1. Boot sequence planner — strict ordered initialisation steps
  2. Driver dependency graph — what must be up before what starts
  3. Cooperative task scheduler design (asyncio / threading model)
  4. Per-module firmware template selection
  5. Code file generator — writes real Python / MicroPython / C files
  6. Optimal code design schematics output (JSON + Markdown)

Principles enforced:
  - Hardware is NEVER touched before power rails are stable
  - SPI bus is single-master arbitrated via CS lock
  - I2C bus uses address reservation before first transaction
  - Display initialised before any user-facing code runs
  - Buttons/joystick polled on a fixed 10ms interrupt timer
  - Battery/power monitoring on a 1Hz background task
  - All drivers communicate through a typed EventBus
  - No busy-waits: all blocking replaced with asyncio.sleep / IRQ callbacks
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from typing import List, Dict, Optional

# ---------------------------------------------------------------------------
# Boot phase constants  (strict ordering — do NOT reorder)
# ---------------------------------------------------------------------------
BOOT_PHASES = [
    {
        "phase": 0,
        "name":  "POWER_RAIL_SETTLE",
        "delay_ms": 50,
        "description": (
            "Wait for all power rails to stabilise after reset. "
            "3.3V LDO requires ~20ms; 5V boost converter ~30ms. "
            "No GPIO activity until this phase completes."
        ),
        "gate": "VCC ≥ 3.1V AND 5V rail ≥ 4.75V",
    },
    {
        "phase": 1,
        "name":  "GPIO_DIRECTION_SET",
        "delay_ms": 1,
        "description": (
            "Set all GPIO pin directions, pull resistors, and initial output states. "
            "CS pins HIGH (deasserted), RST pins HIGH, BL pin LOW (backlight off). "
            "MUST precede any bus transactions."
        ),
        "gate": "All CS pins HIGH before SPI transactions",
    },
    {
        "phase": 2,
        "name":  "BUS_INIT",
        "delay_ms": 5,
        "description": (
            "Initialise SPI bus (mode 0, 8MHz) and I2C bus (400kHz). "
            "Verify bus is idle (MISO line read). "
            "Lock SPI bus arbiter mutex."
        ),
        "gate": "SPI MISO idle HIGH; I2C no ACK collision",
    },
    {
        "phase": 3,
        "name":  "DISPLAY_HARDWARE_RESET",
        "delay_ms": 150,
        "description": (
            "Assert RST LOW for 10µs, then HIGH. Wait 120ms for ST7789 "
            "internal reset to complete. "
            "Backlight remains OFF during reset sequence."
        ),
        "gate": "RST pulse ≥ 10µs; settle ≥ 120ms",
    },
    {
        "phase": 4,
        "name":  "DISPLAY_INIT",
        "delay_ms": 50,
        "description": (
            "Send ST7789 / SSD1306 init command sequence. "
            "Set colour format (RGB565), rotation, sleep-out, display-on. "
            "Fill screen with black before enabling backlight."
        ),
        "gate": "Display responds to MADCTL command",
    },
    {
        "phase": 5,
        "name":  "BACKLIGHT_ON",
        "delay_ms": 10,
        "description": (
            "Enable display backlight (GPIO24 HIGH or PWM fade-in). "
            "Show splash screen / loading indicator."
        ),
        "gate": "Display init complete",
    },
    {
        "phase": 6,
        "name":  "INPUT_INIT",
        "delay_ms": 5,
        "description": (
            "Configure button GPIO pins with interrupt handlers (falling edge, debounce 50ms). "
            "Configure ADC (MCP3008) for joystick; set deadzone ±50 counts. "
            "Start button poll timer at 10ms interval."
        ),
        "gate": "GPIO interrupts armed",
    },
    {
        "phase": 7,
        "name":  "SECONDARY_PERIPHERAL_INIT",
        "delay_ms": 20,
        "description": (
            "Initialise optional peripherals: IMU (MPU-6050), audio (PWM), "
            "NeoPixel (WS2812B), secondary OLED. "
            "Each driver checks I2C address ACK before proceeding. "
            "Absent hardware is logged and skipped — no crash."
        ),
        "gate": "I2C address detect; SPI device ID read",
    },
    {
        "phase": 8,
        "name":  "BATTERY_MONITOR_START",
        "delay_ms": 10,
        "description": (
            "Start 1Hz battery voltage polling task. "
            "Read voltage divider ADC → compute SoC percentage. "
            "Post initial BatteryEvent to EventBus."
        ),
        "gate": "ADC available",
    },
    {
        "phase": 9,
        "name":  "EVENT_BUS_START",
        "delay_ms": 1,
        "description": (
            "Start asyncio event loop. Register all event handlers. "
            "EventBus is the ONLY inter-module communication channel — "
            "no direct module-to-module function calls after this point."
        ),
        "gate": "All drivers registered",
    },
    {
        "phase": 10,
        "name":  "APPLICATION_START",
        "delay_ms": 0,
        "description": (
            "Call app.run(). Application code starts executing. "
            "All hardware is guaranteed ready at this point."
        ),
        "gate": "Phase 9 complete",
    },
]

# ---------------------------------------------------------------------------
# Driver dependency graph
# Maps driver_key → list of driver_keys that MUST be initialised first
# ---------------------------------------------------------------------------
DRIVER_DEPS = {
    "power_rails":    [],
    "gpio":           ["power_rails"],
    "spi_bus":        ["gpio"],
    "i2c_bus":        ["gpio"],
    "display_st7789": ["spi_bus", "gpio"],
    "display_ssd1306":["i2c_bus"],
    "backlight_pwm":  ["gpio", "display_st7789"],
    "buttons":        ["gpio"],
    "joystick_adc":   ["spi_bus", "gpio"],
    "imu_mpu6050":    ["i2c_bus"],
    "audio_pwm":      ["gpio"],
    "neopixel":       ["gpio"],
    "sd_card":        ["spi_bus"],
    "battery_monitor":["spi_bus"],
    "event_bus":      [
        "display_st7789", "backlight_pwm",
        "buttons", "joystick_adc", "battery_monitor",
    ],
    "application":    ["event_bus"],
}

# ---------------------------------------------------------------------------
# Event types flowing over the EventBus
# ---------------------------------------------------------------------------
EVENT_TYPES = {
    "ButtonPressed":    {"fields": ["button_id: str", "timestamp: float"]},
    "ButtonReleased":   {"fields": ["button_id: str", "timestamp: float"]},
    "JoystickMoved":    {"fields": ["x: float", "y: float", "pressed: bool", "timestamp: float"]},
    "DisplayReady":     {"fields": []},
    "DisplayFrame":     {"fields": ["framebuffer: bytes"]},
    "BatteryUpdate":    {"fields": ["voltage_v: float", "soc_pct: int", "charging: bool"]},
    "IMUData":          {"fields": ["accel: tuple", "gyro: tuple", "timestamp: float"]},
    "AudioPlay":        {"fields": ["frequency_hz: int", "duration_ms: int"]},
    "NeopixelSet":      {"fields": ["pixels: list"]},
    "SystemShutdown":   {"fields": ["reason: str"]},
    "ErrorEvent":       {"fields": ["source: str", "message: str", "fatal: bool"]},
}

# ---------------------------------------------------------------------------
# Cooperative scheduler model
# ---------------------------------------------------------------------------
SCHEDULER_DESIGN = {
    "model": "asyncio (single-threaded cooperative, Python 3.11+)",
    "tasks": [
        {
            "name":     "display_render_task",
            "interval_ms": 33,     # ~30fps
            "priority": "HIGH",
            "description": "Composite and push framebuffer to display over SPI",
            "cpu_budget_pct": 40,
        },
        {
            "name":     "input_poll_task",
            "interval_ms": 10,     # 100Hz input polling
            "priority": "HIGH",
            "description": "Read GPIO buttons + joystick input; emit events",
            "cpu_budget_pct": 10,
        },
        {
            "name":     "app_logic_task",
            "interval_ms": 33,     # 30fps game/app tick
            "priority": "NORMAL",
            "description": "Main application state machine update",
            "cpu_budget_pct": 35,
        },
        {
            "name":     "battery_task",
            "interval_ms": 1000,   # 1Hz
            "priority": "LOW",
            "description": "Battery voltage/SoC ADC read; post BatteryUpdate event",
            "cpu_budget_pct": 2,
        },
        {
            "name":     "imu_task",
            "interval_ms": 20,     # 50Hz
            "priority": "NORMAL",
            "description": "MPU-6050 FIFO drain; post IMUData events",
            "cpu_budget_pct": 8,
        },
        {
            "name":     "watchdog_task",
            "interval_ms": 5000,
            "priority": "SYSTEM",
            "description": "Hardware watchdog kick; check task health",
            "cpu_budget_pct": 1,
        },
    ],
    "spi_lock": "asyncio.Lock() — all SPI transactions must acquire before CS assert",
    "i2c_lock": "asyncio.Lock() — shared bus; acquire before every transaction",
    "notes": [
        "No task may hold the SPI lock for more than 2ms (display DMA excepted).",
        "Input events are queued in asyncio.Queue(maxsize=32) — never dropped.",
        "Display render is double-buffered: draw to back buffer, flip on vsync.",
        "All blocking I/O wrapped in asyncio.to_thread() if unavoidable.",
        "SystemShutdown event triggers ordered teardown (reverse of boot order).",
    ],
}
