"""
app/state_machine.py — Generic Finite State Machine
Use this as the backbone of your application logic.
States communicate exclusively through EventBus events.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Callable, Dict, Any

logger = logging.getLogger(__name__)


class State:
    """Base class for application states."""
    name: str = "unnamed"

    async def on_enter(self, ctx: dict) -> None:
        """Called once when entering this state."""

    async def on_exit(self, ctx: dict) -> None:
        """Called once when leaving this state."""

    async def update(self, ctx: dict) -> str | None:
        """
        Called every frame (~30Hz).
        Return next state name to transition, or None to stay.
        """
        return None

    async def on_event(self, event: Any, ctx: dict) -> str | None:
        """
        Called for every EventBus event while in this state.
        Return next state name to transition, or None to stay.
        """
        return None


class StateMachine:
    """
    Cooperative async state machine.
    Transitions are driven by EventBus events or update() return values.
    """

    def __init__(self, initial_state: str):
        self._states: Dict[str, State] = {}
        self._current: str = initial_state
        self._ctx: dict = {}
        self._running = False

    def register(self, state: State) -> "StateMachine":
        self._states[state.name] = state
        return self

    async def transition(self, next_name: str) -> None:
        if next_name not in self._states:
            logger.error(f"[FSM] Unknown state: {next_name}")
            return
        old = self._current
        await self._states[old].on_exit(self._ctx)
        self._current = next_name
        logger.info(f"[FSM] {old} → {next_name}")
        await self._states[next_name].on_enter(self._ctx)

    async def on_event(self, event: Any) -> None:
        state = self._states.get(self._current)
        if state:
            next_name = await state.on_event(event, self._ctx)
            if next_name:
                await self.transition(next_name)

    async def run(self) -> None:
        self._running = True
        await self._states[self._current].on_enter(self._ctx)
        while self._running:
            state = self._states.get(self._current)
            if state:
                next_name = await state.update(self._ctx)
                if next_name:
                    await self.transition(next_name)
            await asyncio.sleep(1 / 30)   # 30Hz update rate

    def stop(self) -> None:
        self._running = False
