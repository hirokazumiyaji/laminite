"""StreamingResponse (#27)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

from laminite import Laminite, StreamingResponse
from laminite.testing import TestClient


@dataclass
class _Conn:
    pass


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


def test_head_closes_async_generator() -> None:
    app = Laminite()
    closed: list[str] = []

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            try:
                yield b"secret"
            finally:
                closed.append("closed")

        return StreamingResponse(gen())

    client = TestClient(app)
    assert client.head("/stream").content == b""
    assert closed == ["closed"]
    client.close()
