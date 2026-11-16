"""
drivers/audio.py — PWM Audio / Buzzer Driver
Simple PWM tone generation for GPIO12/13 (PWM0/PWM1).
For richer audio: use ALSA aplay with WAV files via subprocess.
"""
import asyncio
import subprocess
import RPi.GPIO as GPIO
import logging
from config import AUDIO_L_PIN, AUDIO_R_PIN
from event_bus import bus, AudioPlay

logger = logging.getLogger(__name__)


class AudioDriver:
    def __init__(self):
        self._pwm_l = None
        self._pwm_r = None

    def init(self) -> None:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(AUDIO_L_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(AUDIO_R_PIN, GPIO.OUT, initial=GPIO.LOW)
        self._pwm_l = GPIO.PWM(AUDIO_L_PIN, 440)
        self._pwm_r = GPIO.PWM(AUDIO_R_PIN, 440)
        # Subscribe to AudioPlay events
        bus.subscribe(AudioPlay, self._on_audio_play)
        logger.info(f"[Audio] PWM on BCM{AUDIO_L_PIN}/BCM{AUDIO_R_PIN}")

    async def _on_audio_play(self, event: AudioPlay) -> None:
        await self.tone(event.frequency_hz, event.duration_ms)

    async def tone(self, freq_hz: int, duration_ms: int) -> None:
        """Play a tone at freq_hz for duration_ms milliseconds."""
        if freq_hz <= 0:
            return
        self._pwm_l.ChangeFrequency(freq_hz)
        self._pwm_r.ChangeFrequency(freq_hz)
        self._pwm_l.start(50)   # 50% duty cycle
        self._pwm_r.start(50)
        await asyncio.sleep(duration_ms / 1000)
        self._pwm_l.stop()
        self._pwm_r.stop()

    async def play_wav(self, path: str) -> None:
        """Play a WAV file via ALSA aplay."""
        proc = await asyncio.create_subprocess_exec(
            "aplay", "-q", path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    def close(self) -> None:
        if self._pwm_l: self._pwm_l.stop()
        if self._pwm_r: self._pwm_r.stop()


audio = AudioDriver()
