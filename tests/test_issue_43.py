"""Hot-path allocations and lazy OpenAPI generation (#43)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import msgspec
from msgspec import Struct

from laminite import Laminite, Request, Response, StreamingResponse
from laminite.compiler import analyze_handler
from laminite.di import DependencyRegistry
from laminite.openapi import build_openapi
from laminite.response import send_response
from laminite.testing import TestClient


class Item(Struct):
    name: str


class Svc:
    def __init__(self, name: str) -> None:
        self.name = name


# Two distinct types with the same __name__ collide in OpenAPI components.
_ItemA = msgspec.defstruct("Item", [("name", str)])
_ItemB = msgspec.defstruct("Item", [("qty", int)])


class _CountingHeaders(list[tuple[bytes, bytes]]):
    iterations = 0

    def __iter__(self) -> Any:
        type(self).iterations += 1
        return super().__iter__()


def _scope(**overrides: Any) -> dict[str, Any]:
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/items",
        "raw_path": b"/items",
        "query_string": b"",
        "headers": [],
        "client": ("test", 1),
        "server": ("test", 80),
        "state": {},
    }
    scope.update(overrides)
    return scope


async def _call(app: Laminite, scope: dict[str, Any], body: bytes) -> list[dict[str, Any]]:
    sent = False
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    return messages


def test_body_route_does_not_construct_request() -> None:
    app = Laminite()

    @app.post("/items")
    def create(body: Item) -> dict[str, str]:
        return {"name": body.name}

    client = TestClient(app)
    with patch("laminite.compiler.Request", wraps=Request) as request_cls:
        res = client.post("/items", json={"name": "a"})
        assert res.status_code == 200
        assert res.json() == {"name": "a"}
        request_cls.assert_not_called()
    client.close()


def test_body_route_scans_raw_headers_once() -> None:
    app = Laminite()

    @app.post("/items")
    def create(body: Item) -> dict[str, str]:
        return {"name": body.name}

    app.freeze()
    _CountingHeaders.iterations = 0
    headers = _CountingHeaders(
        [
            (b"content-type", b"application/json"),
            (b"content-length", b"12"),
            (b"x-trace", b"1"),
        ]
    )
    messages = asyncio.run(_call(app, _scope(headers=headers), b'{"name":"a"}'))
    assert any(message["type"] == "http.response.start" for message in messages)
    assert type(headers).iterations == 1


def test_body_route_with_request_argument_reuses_cached_body() -> None:
    app = Laminite()

    @app.post("/items")
    async def create(request: Request, body: Item) -> dict[str, str]:
        raw = await request.body()
        return {"name": body.name, "raw": raw.decode()}

    client = TestClient(app)
    res = client.post("/items", json={"name": "a"})
    assert res.status_code == 200
    assert res.json() == {"name": "a", "raw": '{"name":"a"}'}
    client.close()


def test_exception_handler_can_read_body_after_body_only_route_fails() -> None:
    app = Laminite()

    @app.post("/items")
    def create(body: Item) -> None:
        raise RuntimeError("boom")

    @app.exception_handler(RuntimeError)
    async def handle(request: Request, exc: RuntimeError) -> Response:
        return Response(await request.body(), media_type="application/json")

    client = TestClient(app)
    res = client.post("/items", json={"name": "a"})
    assert res.status_code == 200
    assert res.content == b'{"name":"a"}'
    client.close()


def test_path_only_route_is_flagged_for_fast_kwargs() -> None:
    app = Laminite()

    @app.get("/items/{item_id}")
    def read(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    compiled = analyze_handler(app._routes[0], DependencyRegistry())
    assert compiled.path_params_only is True
    assert compiled.path_params_fast is True
    assert compiled.path_binding_names == ("item_id",)


def test_openapi_is_lazy_and_cached_when_url_is_disabled() -> None:
    app = Laminite()

    @app.get("/")
    def root() -> dict[str, str]:
        return {"ok": "1"}

    with patch("laminite.app.build_openapi", wraps=build_openapi) as build:
        client = TestClient(app)
        assert client.get("/").status_code == 200
        build.assert_not_called()

        doc = app.openapi()
        assert build.call_count == 1
        assert app.openapi() is doc
        client.close()


def test_openapi_cache_is_not_rewritten_by_http_overrides() -> None:
    app = Laminite(openapi_url="/openapi.json")
    app.provide(Svc, Svc("base"))

    @app.get("/")
    def root(svc: Svc) -> dict[str, str]:
        return {"svc": svc.name}

    app.freeze()
    doc = app.openapi()
    app.http_with_overrides({Svc: Svc("override")})
    assert app.openapi() is doc


def test_disabled_openapi_does_not_fail_on_conflicting_struct_names() -> None:
    app = Laminite()

    @app.get("/a")
    def a() -> _ItemA:  # type: ignore[name-defined, valid-type]
        return _ItemA(name="x")

    @app.get("/b")
    def b() -> _ItemB:  # type: ignore[name-defined, valid-type]
        return _ItemB(qty=1)

    client = TestClient(app)
    assert client.get("/a").status_code == 200
    assert client.get("/b").status_code == 200
    client.close()


def test_send_response_does_not_mutate_caller_headers() -> None:
    async def run() -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        headers = [(b"x-a", b"1")]
        await send_response(send, status_code=200, headers=headers, body=b"hi", is_head=False)
        await send_response(send, status_code=200, headers=headers, body=b"hi", is_head=False)
        assert headers == [(b"x-a", b"1")]
        return [message for message in messages if message["type"] == "http.response.start"]

    starts = asyncio.run(run())
    assert [message["headers"] for message in starts] == [
        [(b"x-a", b"1"), (b"content-length", b"2")],
        [(b"x-a", b"1"), (b"content-length", b"2")],
    ]


def test_framing_headers_are_dropped_when_responses_are_built() -> None:
    response = Response(
        b"x",
        headers={
            "Content-Length": "99",
            "Transfer-Encoding": "chunked",
            "X-Keep": "1",
        },
    )
    assert response.raw_headers == [(b"x-keep", b"1")]

    stream = StreamingResponse(
        [b"x"],
        headers=[("Content-Length", "99"), ("Transfer-Encoding", "chunked"), ("X-Keep", "1")],
    )
    assert stream.raw_headers == [(b"x-keep", b"1")]
