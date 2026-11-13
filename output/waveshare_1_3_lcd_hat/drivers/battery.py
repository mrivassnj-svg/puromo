"""
drivers/battery.py — LiPo Battery Monitor
Reads cell voltage via voltage divider → MCP3008 ADC.
Posts BatteryUpdate events at 1Hz.
Triggers SystemShutdown when voltage drops below cut-off.

Wiring:
    BAT+ ──┬── R1(10kΩ) ──── MCP3008 CH2
           └── R2(10kΩ) ──── GND
"""
import asyncio
import logging
from config import (
    BAT_ADC_CH, BAT_VDIV_R1, BAT_VDIV_R2,
    BAT_VREF, BAT_FULL_V, BAT_EMPTY_V, BAT_WARN_PCT,
    BATTERY_POLL_HZ
)
from event_bus import bus, BatteryUpdate, SystemShutdown
from drivers.joystick import MCP3008   # reuse ADC driver

logger = logging.getLogger(__name__)


class BatteryMonitor:
    def __init__(self):
        self._adc = MCP3008()
        self._running = False
        self.voltage = 0.0
        self.soc_pct = 100

    def init(self) -> None:
        # Reuse existing MCP3008 on CE1 — same SPI device
        # NOTE: joystick driver must be initialised first
        logger.info("[Battery] Monitor initialised")

    def _read_voltage(self) -> float:
        """Read ADC and calculate actual battery voltage."""
        raw = self._adc.read(BAT_ADC_CH)
        v_adc = (raw / 1023.0) * BAT_VREF
        # Reverse voltage divider: V_bat = V_adc * (R1+R2)/R2
        v_bat = v_adc * (BAT_VDIV_R1 + BAT_VDIV_R2) / BAT_VDIV_R2
        return round(v_bat, 3)

    def _voltage_to_soc(self, v: float) -> int:
        """Linear approximation of LiPo SoC from voltage."""
        if v >= BAT_FULL_V:
            return 100
        if v <= BAT_EMPTY_V:
            return 0
        return int((v - BAT_EMPTY_V) / (BAT_FULL_V - BAT_EMPTY_V) * 100)

    async def run(self) -> None:
        self._running = True
        interval = 1.0 / BATTERY_POLL_HZ
        while self._running:
            self.voltage = self._read_voltage()
            self.soc_pct = self._voltage_to_soc(self.voltage)
            charging = False  # extend: read TP4056 CHRG pin if wired

            await bus.publish(BatteryUpdate(
                voltage_v=self.voltage,
                soc_pct=self.soc_pct,
                charging=charging,
            ))

            if self.soc_pct <= 0:
                logger.critical(f"[Battery] CRITICAL: {self.voltage}V — shutting down!")
                await bus.publish(SystemShutdown(reason="battery_empty"))
                break

            if self.soc_pct <= BAT_WARN_PCT:
                logger.warning(f"[Battery] Low: {self.soc_pct}% ({self.voltage}V)")

            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._running = False


battery_monitor = BatteryMonitor()
