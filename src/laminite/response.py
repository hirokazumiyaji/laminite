"""Response types and return-value rendering."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import msgspec

_STREAM_SENTINEL = object()

CONTENT_TYPE_JSON = (b"content-type", b"application/json")
CONTENT_TYPE_TEXT = (b"content-type", b"text/plain; charset=utf-8")
CONTENT_TYPE_OCTET = (b"content-type", b"application/octet-stream")

# One encoder for every body, so success and error responses encode identically.
_ENCODER = msgspec.json.Encoder()


def _append_headers(
    raw_headers: list[tuple[bytes, bytes]],
    headers: Mapping[str, str] | None,
    media_type: str | None,
) -> None:
    if headers:
        for key, value in headers.items():
            raw_headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))
    if media_type is not None:
        raw_headers.append((b"content-type", media_type.encode("latin-1")))


class Response:
    def __init__(
        self,
        content: bytes | str = b"",
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
    ) -> None:
        if isinstance(content, str):
            body = content.encode("utf-8")
            if media_type is None:
                media_type = "text/plain; charset=utf-8"
        else:
            body = content
        self.body = body
        self.status_code = status_code
        self.media_type = media_type
        self.raw_headers: list[tuple[bytes, bytes]] = []
        _append_headers(self.raw_headers, headers, media_type)


class JSONResponse(Response):
    def __init__(
        self,
        content: Any,
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        body = _ENCODER.encode(content)
        super().__init__(
            body,
            status_code=status_code,
            headers=headers,
            media_type="application/json",
        )


class StreamingResponse:
    """Chunked ASGI response from a sync or async iterator. No Content-Length."""

    def __init__(
        self,
        content: Iterable[bytes | str] | AsyncIterable[bytes | str],
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
    ) -> None:
        self.body_iterator = content
        self.status_code = status_code
        self.media_type = media_type
        self.raw_headers: list[tuple[bytes, bytes]] = []
        _append_headers(self.raw_headers, headers, media_type)


def render_result(
    result: Any,
    *,
    status_code: int,
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    """Render a handler return value to status, headers, body."""
    renderer = _RENDERERS.get(type(result))
    if renderer is not None:
        return renderer(result, status_code)
    if isinstance(result, Response):
        return result.status_code, list(result.raw_headers), result.body
    return _render_json(result, status_code)


def _render_none(_result: None, status_code: int) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    return status_code, [], b""


def _render_bytes(result: bytes, status_code: int) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    return status_code, [CONTENT_TYPE_OCTET], result


def _render_str(result: str, status_code: int) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    return status_code, [CONTENT_TYPE_TEXT], result.encode("utf-8")


def _render_json(result: Any, status_code: int) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    return status_code, [CONTENT_TYPE_JSON], _ENCODER.encode(result)


_Renderer = Callable[[Any, int], tuple[int, list[tuple[bytes, bytes]], bytes]]

_RENDERERS: dict[type[Any], _Renderer] = {
    type(None): _render_none,
    bytes: _render_bytes,
    str: _render_str,
}


async def send_response(
    send: Any,
    *,
    status_code: int,
    headers: list[tuple[bytes, bytes]],
    body: bytes,
    is_head: bool,
) -> None:
    header_list = list(headers)
    header_list.append((b"content-length", str(len(body)).encode("latin-1")))
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": header_list,
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": b"" if is_head else body,
        }
    )


def _chunk_bytes(chunk: bytes | str) -> bytes:
    if isinstance(chunk, str):
        return chunk.encode("utf-8")
    return chunk


def _sync_next(iterator: Any) -> object:
    return next(iterator, _STREAM_SENTINEL)


def _sync_close(iterator: Any) -> None:
    close = getattr(iterator, "close", None)
    if callable(close):
        close()


def _as_sync_iterator(content: Iterable[bytes | str]) -> Any:
    if hasattr(content, "__next__"):
        return content
    return iter(content)


async def _force_close_async_iterator(iterator: Any) -> None:
    aclose = getattr(iterator, "aclose", None)
    if callable(aclose):
        result = aclose()
        if hasattr(result, "__await__"):
            await result  # type: ignore[misc]


async def send_streaming_response(
    send: Any,
    response: StreamingResponse,
    *,
    is_head: bool,
) -> None:
    content = response.body_iterator
    executor: ThreadPoolExecutor | None = None
    if hasattr(content, "__aiter__"):
        iterator: Any = aiter(content)  # type: ignore[arg-type]
        is_async = True
    else:
        iterator = _as_sync_iterator(content)  # type: ignore[arg-type]
        is_async = False
        # One worker so next() and close() never race across threads.
        executor = ThreadPoolExecutor(max_workers=1)

    loop = asyncio.get_running_loop()

    async def run_sync(fn: Any, *args: Any) -> Any:
        assert executor is not None
        return await loop.run_in_executor(executor, fn, *args)

    try:
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": list(response.raw_headers),
            }
        )

        if is_head:
            # Do not advance the iterator; just close and send an empty body.
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return

        if is_async:
            async for chunk in iterator:
                data = _chunk_bytes(chunk)
                if data:
                    await send({"type": "http.response.body", "body": data, "more_body": True})
        else:
            while True:
                chunk = await run_sync(_sync_next, iterator)
                if chunk is _STREAM_SENTINEL:
                    break
                data = _chunk_bytes(chunk)  # type: ignore[arg-type]
                if data:
                    await send({"type": "http.response.body", "body": data, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})
    finally:
        try:
            if is_async:
                await _force_close_async_iterator(iterator)
            else:
                await run_sync(_sync_close, iterator)
        finally:
            if executor is not None:
                executor.shutdown(wait=True)


def error_json(detail: Any, status_code: int) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    return _render_json({"detail": detail}, status_code)
