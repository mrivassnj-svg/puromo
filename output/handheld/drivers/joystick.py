"""
drivers/joystick.py — MCP3008 ADC Analog Joystick Driver
Reads X/Y axes at 100Hz via SPI CE1.
CS must NOT conflict with display CS (CE0).

Value range: 0–1023 (10-bit ADC).
Normalised output: -1.0 to +1.0 with configurable deadzone.
"""
import asyncio
import spidev
import RPi.GPIO as GPIO
import logging
from config import (
    JOY_SPI_BUS, JOY_CS, JOY_SPEED_HZ,
    JOY_CH_X, JOY_CH_Y, JOY_SW,
    JOY_DEADZONE, JOY_CENTRE, INPUT_POLL_HZ
)
from event_bus import bus, JoystickMoved

logger = logging.getLogger(__name__)


class MCP3008:
    """10-bit SPI ADC driver — 8 channels."""
    def __init__(self):
        self._spi = spidev.SpiDev()

    def open(self, bus_num: int, device: int, speed: int) -> None:
        self._spi.open(bus_num, device)
        self._spi.max_speed_hz = speed
        self._spi.mode = 0
        logger.info(f"[MCP3008] SPI{bus_num}.{device} @ {speed//1000}kHz")

    def read(self, channel: int) -> int:
        """Read a single ADC channel. Returns 0–1023."""
        assert 0 <= channel <= 7
        cmd = [1, (8 + channel) << 4, 0]
        resp = self._spi.xfer2(cmd)
        return ((resp[1] & 0x03) << 8) | resp[2]

    def close(self) -> None:
        self._spi.close()


class JoystickDriver:
    def __init__(self):
        self._adc = MCP3008()
        self._running = False

    def init(self) -> None:
        self._adc.open(JOY_SPI_BUS, 1, JOY_SPEED_HZ)   # CE1
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(JOY_SW, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        logger.info("[Joystick] MCP3008 ADC ready")

    def read_raw(self) -> tuple[int, int]:
        return self._adc.read(JOY_CH_X), self._adc.read(JOY_CH_Y)

    def read_norm(self) -> tuple[float, float]:
        """Normalised -1.0 to +1.0 with deadzone applied."""
        rx, ry = self.read_raw()
        def norm(v):
            d = v - JOY_CENTRE
            if abs(d) < JOY_DEADZONE:
                return 0.0
            return max(-1.0, min(1.0, d / (JOY_CENTRE - JOY_DEADZONE)))
        return norm(rx), norm(ry)

    @property
    def pressed(self) -> bool:
        return not GPIO.input(JOY_SW)

    async def run(self) -> None:
        """Async polling task — runs at INPUT_POLL_HZ."""
        self._running = True
        interval = 1.0 / INPUT_POLL_HZ
        import time
        while self._running:
            x, y = self.read_norm()
            pressed = self.pressed
            if abs(x) > 0.01 or abs(y) > 0.01 or pressed:
                await bus.publish(JoystickMoved(
                    x=x, y=y, pressed=pressed, timestamp=time.monotonic()
                ))
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self.stop()
        self._adc.close()


joystick = JoystickDriver()
