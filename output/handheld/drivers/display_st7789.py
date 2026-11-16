"""
drivers/display_st7789.py — ST7789 SPI Display Driver
Supports: Waveshare 1.3" / 2" LCD HAT, any ST7789-based panel.

Boot order requirement: spi_bus + gpio must be initialised first.
Backlight is managed separately via drivers/backlight.py.
"""
import time
import struct
import logging
import spidev
import RPi.GPIO as GPIO
from config import (
    SPI_BUS, SPI_SPEED_HZ, DISP_DC, DISP_RST, DISP_CS,
    DISP_WIDTH, DISP_HEIGHT, DISP_ROTATION
)
from event_bus import bus, DisplayReady, ErrorEvent

logger = logging.getLogger(__name__)

# ST7789 command constants
_NOP       = 0x00
_SWRESET   = 0x01
_SLPOUT    = 0x11
_NORON     = 0x13
_INVON     = 0x21
_DISPON    = 0x29
_CASET     = 0x2A
_RASET     = 0x2B
_RAMWR     = 0x2C
_MADCTL    = 0x36
_COLMOD    = 0x3A
_PORCTRL   = 0xB2
_GCTRL     = 0xB7
_VCOMS     = 0xBB
_LCMCTRL   = 0xC0
_VDVVRHEN  = 0xC2
_VRHS      = 0xC3
_VDVS      = 0xC4
_FRCTRL2   = 0xC6
_PWCTRL1   = 0xD0
_PVGAMCTRL = 0xE0
_NVGAMCTRL = 0xE1

# MADCTL rotation flags
_MADCTL_MY  = 0x80
_MADCTL_MX  = 0x40
_MADCTL_MV  = 0x20
_MADCTL_RGB = 0x00
_MADCTL_BGR = 0x08

ROTATION_MAP = {
    0:   _MADCTL_MX | _MADCTL_RGB,
    90:  _MADCTL_MV | _MADCTL_RGB,
    180: _MADCTL_MY | _MADCTL_RGB,
    270: _MADCTL_MX | _MADCTL_MV | _MADCTL_MY | _MADCTL_RGB,
}

# Full ST7789 init sequence (Waveshare HAT verified)
INIT_SEQUENCE = [
    (_SWRESET,  [],       150),   # software reset, wait 150ms
    (_SLPOUT,   [],       120),   # sleep out, wait 120ms
    (_COLMOD,   [0x55],     0),   # RGB565 (5-6-5)
    (_MADCTL,   [ROTATION_MAP[DISP_ROTATION]], 0),
    (_PORCTRL,  [0x0C, 0x0C, 0x00, 0x33, 0x33], 0),
    (_GCTRL,    [0x35],     0),
    (_VCOMS,    [0x19],     0),
    (_LCMCTRL,  [0x2C],     0),
    (_VDVVRHEN, [0x01],     0),
    (_VRHS,     [0x12],     0),
    (_VDVS,     [0x20],     0),
    (_FRCTRL2,  [0x0F],     0),
    (_PWCTRL1,  [0xA4, 0xA1], 0),
    (_PVGAMCTRL,[0xD0,0x04,0x0D,0x11,0x13,0x2B,0x3F,0x54,0x4C,0x18,0x0D,0x0B,0x1F,0x23], 0),
    (_NVGAMCTRL,[0xD0,0x04,0x0C,0x11,0x13,0x2C,0x3F,0x44,0x51,0x2F,0x1F,0x1F,0x20,0x23], 0),
    (_INVON,    [],         0),
    (_NORON,    [],        10),
    (_DISPON,   [],         0),
]


class ST7789:
    """
    Low-level ST7789 SPI display driver.
    All pixel data is RGB565 big-endian.
    Thread-safety: caller must hold SPI bus lock.
    """

    def __init__(self):
        self._spi  = None
        self._buf  = bytearray(DISP_WIDTH * DISP_HEIGHT * 2)  # back buffer
        self.width  = DISP_WIDTH
        self.height = DISP_HEIGHT
        logger.info(f"[ST7789] {DISP_WIDTH}×{DISP_HEIGHT} rotation={DISP_ROTATION}°")

    # ── Initialisation ───────────────────────────────────────────

    def init(self) -> None:
        """Phase 3+4: hardware reset then command sequence init."""
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(DISP_DC,  GPIO.OUT, initial=GPIO.HIGH)
        GPIO.setup(DISP_RST, GPIO.OUT, initial=GPIO.HIGH)
        GPIO.setup(DISP_CS,  GPIO.OUT, initial=GPIO.HIGH)

        self._spi = spidev.SpiDev()
        self._spi.open(SPI_BUS, 0)
        self._spi.max_speed_hz = SPI_SPEED_HZ
        self._spi.mode = 0
        self._spi.bits_per_word = 8
        self._spi.lsbfirst = False

        self._hardware_reset()
        self._run_init_sequence()
        self.fill(0x0000)  # black screen before backlight on
        logger.info("[ST7789] Init complete")
        # Notify system display is ready
        import asyncio
        try:
            asyncio.get_event_loop().run_until_complete(bus.publish(DisplayReady()))
        except RuntimeError:
            bus.publish_sync(DisplayReady())

    def _hardware_reset(self) -> None:
        """RST pulse: HIGH → LOW (10µs) → HIGH → settle 120ms."""
        GPIO.output(DISP_RST, GPIO.HIGH); time.sleep(0.001)
        GPIO.output(DISP_RST, GPIO.LOW);  time.sleep(0.00001)   # 10µs
        GPIO.output(DISP_RST, GPIO.HIGH); time.sleep(0.120)     # 120ms settle
        logger.debug("[ST7789] Hardware reset done")

    def _run_init_sequence(self) -> None:
        for cmd, data, delay_ms in INIT_SEQUENCE:
            self._cmd(cmd)
            if data:
                self._data(bytes(data))
            if delay_ms:
                time.sleep(delay_ms / 1000)

    # ── Low-level SPI ────────────────────────────────────────────

    def _cmd(self, cmd: int) -> None:
        GPIO.output(DISP_DC, GPIO.LOW)
        GPIO.output(DISP_CS, GPIO.LOW)
        self._spi.writebytes([cmd])
        GPIO.output(DISP_CS, GPIO.HIGH)

    def _data(self, data: bytes) -> None:
        GPIO.output(DISP_DC, GPIO.HIGH)
        GPIO.output(DISP_CS, GPIO.LOW)
        # Chunk writes to avoid spidev 4096-byte limit
        for i in range(0, len(data), 4096):
            self._spi.writebytes2(data[i:i+4096])
        GPIO.output(DISP_CS, GPIO.HIGH)

    # ── Drawing API ──────────────────────────────────────────────

    def _set_window(self, x0:int, y0:int, x1:int, y1:int) -> None:
        self._cmd(_CASET)
        self._data(struct.pack(">HH", x0, x1))
        self._cmd(_RASET)
        self._data(struct.pack(">HH", y0, y1))
        self._cmd(_RAMWR)

    def fill(self, colour: int) -> None:
        """Fill entire display with RGB565 colour."""
        self._set_window(0, 0, self.width-1, self.height-1)
        chunk = struct.pack(">H", colour) * 128
        total = self.width * self.height
        GPIO.output(DISP_DC, GPIO.HIGH)
        GPIO.output(DISP_CS, GPIO.LOW)
        for _ in range(total // 128):
            self._spi.writebytes2(chunk)
        rem = total % 128
        if rem:
            self._spi.writebytes2(struct.pack(">H", colour) * rem)
        GPIO.output(DISP_CS, GPIO.HIGH)

    def blit(self, buf: bytes,
             x: int = 0, y: int = 0,
             w: int = DISP_WIDTH, h: int = DISP_HEIGHT) -> None:
        """Push raw RGB565 buffer to display window."""
        self._set_window(x, y, x+w-1, y+h-1)
        self._data(buf)

    def pixel(self, x: int, y: int, colour: int) -> None:
        self._set_window(x, y, x, y)
        self._data(struct.pack(">H", colour))

    def close(self) -> None:
        if self._spi:
            self._spi.close()
        GPIO.cleanup()

    # ── Utility ──────────────────────────────────────────────────

    @staticmethod
    def rgb(r: int, g: int, b: int) -> int:
        """Convert 8-bit RGB to RGB565."""
        return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

    @staticmethod
    def colour565(r: int, g: int, b: int) -> int:
        return ST7789.rgb(r, g, b)


# Module-level singleton
display = ST7789()
