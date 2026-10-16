"""Typing, versioning, and ASGI scope quality checks (#24)."""

from __future__ import annotations

import asyncio

import pytest

from laminite import Laminite
from laminite.testing import TestClient


def test_unsupported_scope_type_raises() -> None:
    app = Laminite()

    @app.get("/")
    def root() -> dict[str, str]:
        return {"ok": "1"}

    async def call() -> None:
        scope = {"type": "unknown", "asgi": {"version": "3.0"}}

        async def receive() -> dict[str, object]:
            return {"type": "http.disconnect"}

        async def send(_message: dict[str, object]) -> None:
            return None

        with pytest.raises(RuntimeError, match="Unsupported ASGI scope type"):
            await app(scope, receive, send)

    asyncio.run(call())


def test_websocket_scope_is_closed() -> None:
    app = Laminite()

    @app.get("/")
    def root() -> dict[str, str]:
        return {"ok": "1"}

    async def call() -> list[dict[str, object]]:
        scope = {"type": "websocket", "asgi": {"version": "3.0"}, "path": "/"}
        messages: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return {"type": "websocket.disconnect"}

        async def send(message: dict[str, object]) -> None:
            messages.append(message)

        await app(scope, receive, send)
        return messages

    assert asyncio.run(call()) == [{"type": "websocket.close", "code": 1000}]


def test_put_and_patch_routes() -> None:
    app = Laminite()

    @app.put("/items/{item_id}")
    def put_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @app.patch("/items/{item_id}")
    def patch_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    client = TestClient(app)
    assert client.put("/items/1").json() == {"item_id": 1}
    assert client.patch("/items/2").json() == {"item_id": 2}
    client.close()


def test_set_overrides_rejected_when_frozen() -> None:
    app = Laminite()

    @app.get("/")
    def root() -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app)
    client.get("/")
    client.close()
    with pytest.raises(RuntimeError, match="frozen"):
        app.set_overrides({})
