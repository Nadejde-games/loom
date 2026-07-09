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
from .engine import Engine

__all__ = ["Message", "Channel", "Session", "GameServer", "GameLoop", "Engine"]
__version__ = "0.0.1"
