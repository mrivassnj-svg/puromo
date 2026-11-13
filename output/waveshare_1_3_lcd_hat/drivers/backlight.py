"""
drivers/backlight.py — PWM Backlight Controller
Controls display backlight brightness via GPIO24 PWM on RPi.
"""
import RPi.GPIO as GPIO
import logging
from config import DISP_BL, BL_PWM_FREQ, BL_DEFAULT, BL_MIN

logger = logging.getLogger(__name__)


class Backlight:
    def __init__(self):
        self._pwm: GPIO.PWM | None = None
        self._level = 0

    def init(self) -> None:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(DISP_BL, GPIO.OUT, initial=GPIO.LOW)
        self._pwm = GPIO.PWM(DISP_BL, BL_PWM_FREQ)
        self._pwm.start(0)
        logger.info(f"[Backlight] PWM on BCM{DISP_BL} @ {BL_PWM_FREQ}Hz")

    def set(self, percent: int) -> None:
        """Set brightness 0–100%."""
        level = max(BL_MIN, min(100, percent))
        if self._pwm:
            self._pwm.ChangeDutyCycle(level)
        self._level = level

    def on(self) -> None:
        self.set(BL_DEFAULT)

    def off(self) -> None:
        if self._pwm:
            self._pwm.ChangeDutyCycle(0)
        self._level = 0

    async def fade_in(self, target: int = BL_DEFAULT, steps: int = 20) -> None:
        import asyncio
        step_size = target / steps
        for i in range(steps):
            self.set(int(step_size * (i + 1)))
            await asyncio.sleep(0.02)

    async def fade_out(self, steps: int = 20) -> None:
        import asyncio
        step_size = self._level / steps
        for i in range(steps, 0, -1):
            self.set(int(step_size * i))
            await asyncio.sleep(0.02)
        self.off()

    def close(self) -> None:
        self.off()
        if self._pwm:
            self._pwm.stop()


backlight = Backlight()
