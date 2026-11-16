"""
app/app.py — Application Base Class for handheld
Extend this class to build your application.
All hardware access goes through the EventBus.
"""
import asyncio
import logging
from event_bus import bus, ButtonPressed, JoystickMoved, BatteryUpdate, SystemShutdown
from app.state_machine import StateMachine, State

logger = logging.getLogger(__name__)


# ─── Example states ──────────────────────────────────────────────

class MenuState(State):
    name = "menu"

    async def on_enter(self, ctx):
        logger.info("[App] Entering menu")
        ctx["menu_index"] = 0

    async def on_event(self, event, ctx):
        if isinstance(event, ButtonPressed):
            if event.button_id == "KEY1":
                ctx["menu_index"] = (ctx["menu_index"] - 1) % 3
            elif event.button_id == "KEY2":
                return "game"      # transition to game state
            elif event.button_id == "KEY3":
                ctx["menu_index"] = (ctx["menu_index"] + 1) % 3
        return None


class GameState(State):
    name = "game"

    async def on_enter(self, ctx):
        logger.info("[App] Entering game")

    async def on_event(self, event, ctx):
        if isinstance(event, ButtonPressed) and event.button_id == "KEY1":
            return "menu"          # back to menu
        return None


class ShutdownState(State):
    name = "shutdown"

    async def on_enter(self, ctx):
        logger.info("[App] System shutdown")
        import RPi.GPIO as GPIO
        GPIO.cleanup()


# ─── Application ─────────────────────────────────────────────────

class HandheldApp:
    """
    Main application class.
    Instantiate in main.py after all drivers are initialised.
    """

    def __init__(self):
        self.fsm = StateMachine(initial_state="menu")
        self.fsm.register(MenuState())
        self.fsm.register(GameState())
        self.fsm.register(ShutdownState())

        # Route ALL events to FSM
        for event_type in [ButtonPressed, JoystickMoved, BatteryUpdate, SystemShutdown]:
            bus.subscribe(event_type, self.fsm.on_event)

    async def run(self):
        logger.info("[App] Starting handheld")
        await self.fsm.run()
