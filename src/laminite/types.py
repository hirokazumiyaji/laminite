"""Public ASGI typing helpers for middleware authors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

type Scope = dict[str, Any]
type Message = dict[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

__all__ = ["ASGIApp", "Message", "Receive", "Scope", "Send"]
