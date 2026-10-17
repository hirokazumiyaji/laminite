"""StreamingResponse (#27)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from laminite import Laminite, StreamingResponse
from laminite.testing import TestClient


@dataclass
class _Conn:
    pass


def _http_scope(path: str) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": ("test", 1),
        "server": ("test", 80),
        "state": {},
    }


def test_sync_generator_streaming() -> None:
    app = Laminite()

    @app.get("/stream")
    def stream() -> StreamingResponse:
        def gen() -> Iterator[bytes]:
            yield b"one"
            yield b"two"

        return StreamingResponse(gen())

    client = TestClient(app)
    res = client.get("/stream")
    assert res.status_code == 200
    assert res.content == b"onetwo"
    assert "content-length" not in res.headers
    client.close()


def test_async_generator_streaming() -> None:
    app = Laminite()

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def gen() -> AsyncIterator[str]:
            yield "a"
            yield "b"

        return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")

    client = TestClient(app)
    res = client.get("/stream")
    assert res.content == b"ab"
    assert res.headers["content-type"] == "text/plain; charset=utf-8"
    client.close()


def test_sse_media_type() -> None:
    app = Laminite()

    @app.get("/events")
    def events() -> StreamingResponse:
        def gen() -> Iterator[str]:
            yield "data: hello\n\n"
            yield "data: world\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    client = TestClient(app)
    res = client.get("/events")
    assert res.headers["content-type"] == "text/event-stream"
    assert res.text == "data: hello\n\ndata: world\n\n"
    client.close()


def test_provider_cleanup_waits_until_stream_ends() -> None:
    app = Laminite()
    seen: list[str] = []

    @app.provider
    def conn() -> Iterator[_Conn]:
        seen.append("enter")
        yield _Conn()
        seen.append("exit")

    @app.get("/stream")
    def stream(c: _Conn) -> StreamingResponse:
        def gen() -> Iterator[bytes]:
            seen.append("chunk1")
            yield b"x"
            seen.append("chunk2")
            yield b"y"

        return StreamingResponse(gen())

    client = TestClient(app)
    assert client.get("/stream").content == b"xy"
    assert seen == ["enter", "chunk1", "chunk2", "exit"]
    client.close()


def test_head_streaming_sends_empty_body() -> None:
    app = Laminite()

    @app.get("/stream")
    def stream() -> StreamingResponse:
        return StreamingResponse([b"secret"], media_type="text/plain")

    client = TestClient(app)
    res = client.head("/stream")
    assert res.status_code == 200
    assert res.content == b""
    client.close()


def test_streaming_skips_empty_chunks_and_accepts_headers() -> None:
    app = Laminite()

    @app.get("/stream")
    def stream() -> StreamingResponse:
        def gen() -> Iterator[bytes]:
            yield b""
            yield b"ok"
            yield b""

        return StreamingResponse(gen(), headers={"x-stream": "1"})

    client = TestClient(app)
    res = client.get("/stream")
    assert res.content == b"ok"
    assert res.headers["x-stream"] == "1"
    client.close()


def test_async_streaming_skips_empty_chunks() -> None:
    app = Laminite()

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            yield b""
            yield b"z"

        return StreamingResponse(gen())

    client = TestClient(app)
    assert client.get("/stream").content == b"z"
    client.close()


def test_list_iterable_streaming() -> None:
    app = Laminite()

    @app.get("/stream")
    def stream() -> StreamingResponse:
        return StreamingResponse([b"one", b"two", "three"])

    client = TestClient(app)
    assert client.get("/stream").content == b"onetwothree"
    client.close()


def test_custom_async_iterable_streaming() -> None:
    class Chunks:
        def __aiter__(self) -> AsyncIterator[bytes]:
            async def gen() -> AsyncIterator[bytes]:
                yield b"x"
                yield b"y"

            return gen()

    app = Laminite()

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        return StreamingResponse(Chunks())

    client = TestClient(app)
    assert client.get("/stream").content == b"xy"
    client.close()


def test_streaming_iterator_error_is_swallowed_after_start() -> None:
    app = Laminite()

    @app.get("/stream")
    def stream() -> StreamingResponse:
        def gen() -> Iterator[bytes]:
            yield b"a"
            raise RuntimeError("boom")

        return StreamingResponse(gen())

    client = TestClient(app)
    # Start already sent; pipeline logs and returns without a replacement body.
    res = client.get("/stream")
    assert res.content == b"a"
    client.close()


def test_response_bytes_content_without_media_type() -> None:
    from laminite.response import Response

    resp = Response(b"raw")
    assert resp.body == b"raw"
    assert resp.media_type is None


def test_streaming_iterator_construction_failure_returns_500() -> None:
    app = Laminite()

    class _Boom:
        def __iter__(self) -> Iterator[bytes]:
            raise RuntimeError("iter failed")

    @app.get("/stream")
    def stream() -> StreamingResponse:
        return StreamingResponse(_Boom())

    client = TestClient(app)
    res = client.get("/stream")
    assert res.status_code == 500
    assert res.json() == {"detail": "Internal Server Error"}
    client.close()


def test_head_closes_async_iterator_without_advancing() -> None:
    closed: list[str] = []
    advanced: list[str] = []

    class Tracked:
        def __aiter__(self) -> AsyncIterator[bytes]:
            return self

        async def __anext__(self) -> bytes:
            advanced.append("next")
            raise StopAsyncIteration

        async def aclose(self) -> None:
            closed.append("closed")

    app = Laminite()

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        return StreamingResponse(Tracked())

    client = TestClient(app)
    assert client.head("/stream").content == b""
    assert advanced == []
    assert closed == ["closed"]
    client.close()


def test_mid_stream_error_still_terminates_response() -> None:
    app = Laminite()

    @app.get("/stream")
    def stream() -> StreamingResponse:
        def gen() -> Iterator[bytes]:
            yield b"a"
            raise RuntimeError("boom")

        return StreamingResponse(gen())

    async def call() -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        await app(_http_scope("/stream"), receive, send)
        return messages

    messages = asyncio.run(call())
    starts = [m for m in messages if m["type"] == "http.response.start"]
    assert len(starts) == 1
    assert messages[-1] == {"type": "http.response.body", "body": b"", "more_body": False}


def test_cancelled_stream_still_closes_providers() -> None:
    app = Laminite()
    seen: list[str] = []

    @app.provider
    async def conn() -> AsyncIterator[_Conn]:
        seen.append("enter")
        try:
            yield _Conn()
        finally:
            await asyncio.sleep(0)
            seen.append("exit")

    @app.get("/stream")
    async def stream(c: _Conn) -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            yield b"a"
            await asyncio.sleep(30)

        return StreamingResponse(gen())

    async def main() -> None:
        receive_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        send_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def receive() -> dict[str, Any]:
            return await receive_q.get()

        async def send(message: dict[str, Any]) -> None:
            await send_q.put(message)

        lifespan = asyncio.create_task(app({"type": "lifespan"}, receive, send))
        await receive_q.put({"type": "lifespan.startup"})
        assert (await send_q.get())["type"] == "lifespan.startup.complete"

        sent: list[dict[str, Any]] = []

        async def receive_http() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send_http(message: dict[str, Any]) -> None:
            sent.append(message)

        request = asyncio.create_task(app(_http_scope("/stream"), receive_http, send_http))
        while not sent:
            await asyncio.sleep(0)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert seen == ["enter", "exit"]

        await receive_q.put({"type": "lifespan.shutdown"})
        await send_q.get()
        await lifespan

    asyncio.run(main())


def test_streaming_keyboard_interrupt_propagates() -> None:
    app = Laminite()

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            yield b"a"
            raise KeyboardInterrupt

        return StreamingResponse(gen())

    async def call() -> None:
        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            pass

        await app(_http_scope("/stream"), receive, send)

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(call())


def test_provider_teardown_failure_after_stream_does_not_send_second_response() -> None:
    app = Laminite()

    @app.provider
    async def conn() -> AsyncIterator[_Conn]:
        yield _Conn()
        raise RuntimeError("teardown boom")

    @app.get("/stream")
    async def stream(c: _Conn) -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            yield b"a"

        return StreamingResponse(gen())

    async def call() -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        await app(_http_scope("/stream"), receive, send)
        return messages

    messages = asyncio.run(call())
    starts = [m for m in messages if m["type"] == "http.response.start"]
    assert len(starts) == 1
    assert messages[-1] == {"type": "http.response.body", "body": b"", "more_body": False}
