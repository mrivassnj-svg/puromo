"""
drivers/buttons.py — Debounced Button Driver
Waveshare HAT: KEY1=GPIO21, KEY2=GPIO20, KEY3=GPIO16 (all active-LOW).
5-way joystick direction pins: UP=6 DOWN=19 LEFT=5 RIGHT=26 PRESS=13

Uses RPi.GPIO edge detection (IRQ-driven) for instant response.
Debounce is enforced in software (50ms window).
Events are posted to EventBus via publish_sync (IRQ-safe).
"""
import time
import logging
import RPi.GPIO as GPIO
from config import BTN_DEBOUNCE_MS, BUTTONS
from event_bus import bus, ButtonPressed, ButtonReleased

logger = logging.getLogger(__name__)

# Waveshare 5-way joystick direction pins (also act as buttons)
JOY_PINS = {
    "JOY_UP":    6,
    "JOY_DOWN":  19,
    "JOY_LEFT":  5,
    "JOY_RIGHT": 26,
    "JOY_PRESS": 13,
}

ALL_BUTTONS = {**BUTTONS, **JOY_PINS}


class ButtonDriver:
    def __init__(self):
        self._last_press: dict = {}   # pin → last press time
        self._state: dict = {}        # pin → current state (True=pressed)

    def init(self) -> None:
        GPIO.setmode(GPIO.BCM)
        for label, pin in ALL_BUTTONS.items():
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(
                pin,
                GPIO.BOTH,
                callback=self._make_callback(label, pin),
                bouncetime=BTN_DEBOUNCE_MS,
            )
            self._state[label] = False
        logger.info(f"[Buttons] {len(ALL_BUTTONS)} buttons armed: {list(ALL_BUTTONS.keys())}")

    def _make_callback(self, label: str, pin: int):
        def _cb(channel):
            now = time.monotonic()
            pressed = not GPIO.input(pin)   # active-LOW
            # Extra software debounce
            last = self._last_press.get(label, 0)
            if (now - last) < BTN_DEBOUNCE_MS / 1000:
                return
            self._last_press[label] = now
            self._state[label] = pressed
            if pressed:
                bus.publish_sync(ButtonPressed(button_id=label, timestamp=now))
                logger.debug(f"[Buttons] PRESS {label}")
            else:
                bus.publish_sync(ButtonReleased(button_id=label, timestamp=now))
                logger.debug(f"[Buttons] RELEASE {label}")
        return _cb

    def is_pressed(self, label: str) -> bool:
        return self._state.get(label, False)

    def close(self) -> None:
        for pin in ALL_BUTTONS.values():
            try:
                GPIO.remove_event_detect(pin)
            except Exception:
                pass


buttons = ButtonDriver()
