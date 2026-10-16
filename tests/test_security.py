"""Security hardening: CRLF headers, Content-Length, query limits, Content-Type (#22)."""

from __future__ import annotations

import asyncio

import pytest
from msgspec import Struct

from laminite import HTTPException, Laminite, Response
from laminite.testing import TestClient


class Item(Struct):
    name: str


def test_response_rejects_crlf_in_headers() -> None:
    with pytest.raises(ValueError):
        Response(b"ok", headers={"x-a": "one\r\ntwo"})
    with pytest.raises(ValueError):
        Response(b"ok", headers={"x-\ninject": "v"})
    with pytest.raises(ValueError):
        Response(b"ok", headers={"x-a": "nul\x00byte"})
    with pytest.raises(ValueError):
        Response(b"ok", headers={"x-test: injected": "value"})


def test_http_exception_rejects_crlf_in_headers() -> None:
    with pytest.raises(ValueError):
        HTTPException(400, headers={"x-a": "one\ntwo"})
    with pytest.raises(ValueError):
        HTTPException(400, headers={"bad\rname": "v"})
    with pytest.raises(ValueError):
        HTTPException(400, headers={"not a token": "v"})


def test_content_length_over_limit_returns_413_without_receive() -> None:
    app = Laminite(max_body_size=10)

    @app.post("/items")
    async def create(item: Item) -> dict[str, str]:
        return {"name": item.name}

    receive_calls = {"n": 0}

    async def call() -> int:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/items",
            "raw_path": b"/items",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", b"100"),
            ],
            "client": ("test", 1),
            "server": ("test", 80),
            "state": {},
        }

        async def receive() -> dict:
            receive_calls["n"] += 1
            return {"type": "http.request", "body": b"{}", "more_body": False}

        messages: list[dict] = []

        async def send(message: dict) -> None:
            messages.append(message)

        await app(scope, receive, send)
        return next(m["status"] for m in messages if m["type"] == "http.response.start")

    status = asyncio.run(call())
    assert status == 413
    assert receive_calls["n"] == 0


def test_invalid_content_length_returns_400() -> None:
    app = Laminite(max_body_size=100)

    @app.post("/items")
    async def create(item: Item) -> dict[str, str]:
        return {"name": item.name}

    client = TestClient(app)
    res = client.post(
        "/items",
        content=b'{"name":"a"}',
        headers={"content-type": "application/json", "content-length": "abc"},
    )
    assert res.status_code == 400
    client.close()


def test_max_query_fields_returns_400() -> None:
    app = Laminite(max_query_fields=3)

    @app.get("/search")
    def search(a: str = "x", b: str = "y") -> dict[str, str]:
        return {"a": a, "b": b}

    client = TestClient(app)
    res = client.get("/search", query={"a": "1", "b": "2", "c": "3", "d": "4"})
    assert res.status_code == 400
    assert client.get("/search", query={"a": "1", "b": "2"}).status_code == 200
    client.close()


def test_json_body_requires_json_content_type() -> None:
    app = Laminite()

    @app.post("/items")
    async def create(item: Item) -> dict[str, str]:
        return {"name": item.name}

    client = TestClient(app)
    res = client.post("/items", content=b'{"name":"a"}', headers={"content-type": "text/plain"})
    assert res.status_code == 415

    ok = client.post("/items", json={"name": "a"})
    assert ok.status_code == 200
    assert ok.json() == {"name": "a"}

    plus = client.post(
        "/items",
        content=b'{"name":"b"}',
        headers={"content-type": "application/vnd.api+json"},
    )
    assert plus.status_code == 200
    client.close()


def test_route_max_body_size_override() -> None:
    app = Laminite(max_body_size=1_000_000)

    @app.post("/small", max_body_size=5)
    async def small(item: Item) -> dict[str, str]:
        return {"name": item.name}

    receive_calls = {"n": 0}

    async def call() -> int:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/small",
            "raw_path": b"/small",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", b"20"),
            ],
            "client": ("test", 1),
            "server": ("test", 80),
            "state": {},
        }

        async def receive() -> dict:
            receive_calls["n"] += 1
            return {"type": "http.request", "body": b"{}", "more_body": False}

        messages: list[dict] = []

        async def send(message: dict) -> None:
            messages.append(message)

        await app(scope, receive, send)
        return next(m["status"] for m in messages if m["type"] == "http.response.start")

    assert asyncio.run(call()) == 413
    assert receive_calls["n"] == 0
