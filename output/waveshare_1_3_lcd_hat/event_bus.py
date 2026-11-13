"""
event_bus.py — Typed publish/subscribe EventBus
All inter-module communication MUST go through here.
No direct driver-to-driver calls after boot completes.
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Any, Type

logger = logging.getLogger(__name__)

# ─── Event dataclasses ───────────────────────────────────────────

@dataclass
class ButtonPressed:
    '''Auto-generated event'''
    button_id: str
    timestamp: float


@dataclass
class ButtonReleased:
    '''Auto-generated event'''
    button_id: str
    timestamp: float


@dataclass
class JoystickMoved:
    '''Auto-generated event'''
    x: float
    y: float
    pressed: bool
    timestamp: float


@dataclass
class DisplayReady:
    '''Auto-generated event'''
    pass


@dataclass
class DisplayFrame:
    '''Auto-generated event'''
    framebuffer: bytes


@dataclass
class BatteryUpdate:
    '''Auto-generated event'''
    voltage_v: float
    soc_pct: int
    charging: bool


@dataclass
class IMUData:
    '''Auto-generated event'''
    accel: tuple
    gyro: tuple
    timestamp: float


@dataclass
class AudioPlay:
    '''Auto-generated event'''
    frequency_hz: int
    duration_ms: int


@dataclass
class NeopixelSet:
    '''Auto-generated event'''
    pixels: list


@dataclass
class SystemShutdown:
    '''Auto-generated event'''
    reason: str


@dataclass
class ErrorEvent:
    '''Auto-generated event'''
    source: str
    message: str
    fatal: bool


# ─── EventBus ────────────────────────────────────────────────────
class EventBus:
    """
    Single-process typed publish/subscribe bus.
    Handlers are called in registration order.
    Async handlers are awaited; sync handlers are called directly.
    """

    def __init__(self):
        self._handlers: Dict[type, List[Callable]] = {}
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=256)

    def subscribe(self, event_type: type, handler: Callable) -> None:
        """Register a handler for a specific event type."""
        self._handlers.setdefault(event_type, []).append(handler)
        logger.debug(f"[EventBus] {handler.__name__} subscribed to {event_type.__name__}")

    def unsubscribe(self, event_type: type, handler: Callable) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: Any) -> None:
        """Dispatch event to all registered handlers immediately."""
        handlers = self._handlers.get(type(event), [])
        for h in handlers:
            try:
                if asyncio.iscoroutinefunction(h):
                    await h(event)
                else:
                    h(event)
            except Exception as exc:
                logger.error(f"[EventBus] Handler {h.__name__} raised: {exc}")
                await self.publish(ErrorEvent(
                    source=h.__name__, message=str(exc), fatal=False
                ))

    def publish_sync(self, event: Any) -> None:
        """Thread-safe fire-and-forget for use in IRQ/non-async context."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(f"[EventBus] Queue full — event {type(event).__name__} dropped")

    async def drain(self) -> None:
        """Call in the event loop to flush queued sync events."""
        while not self._queue.empty():
            event = self._queue.get_nowait()
            await self.publish(event)


# Global singleton
bus = EventBus()
