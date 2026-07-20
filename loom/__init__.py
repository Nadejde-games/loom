"""Loom — a framework for AI-driven, text-first, server-client game worlds.

Working name; rename freely. The framework is game-agnostic: it provides the
server, the message protocol, the world-model primitives, the continuous game
loop, and the AI layer. A *game* is content and configuration built on top
(see the sibling ``game/`` package for the first example world).
"""
from .protocol import Message, Channel
from .session import Session
from .server import GameServer
from .loop import GameLoop
from .chronicle import Chronicle, ChronicleEvent
from .world import Condition, Conditions
from .clock import WorldClock, Phase
from .weather import WeatherSystem, Weather
from .engine import Engine
from .action import (
    ActionRegistry, ActionSpec, ActionIntent, ActionContext, ActionResult,
    Param, ActionError, default_registry,
)
from .salience import SalienceGate, SalienceContext, default_gate
from .naming import resolve, Resolved, Ambiguous, NoMatch
from .identity import PlayerRecord

__all__ = [
    "Message", "Channel", "Session", "GameServer", "GameLoop", "Engine",
    "Chronicle", "ChronicleEvent", "Condition", "Conditions",
    "WorldClock", "Phase", "WeatherSystem", "Weather",
    "ActionRegistry", "ActionSpec", "ActionIntent", "ActionContext",
    "ActionResult", "Param", "ActionError", "default_registry",
    "SalienceGate", "SalienceContext", "default_gate",
    "resolve", "Resolved", "Ambiguous", "NoMatch",
    "PlayerRecord",
]
__version__ = "0.0.1"
