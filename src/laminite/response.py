"""Response types and return-value rendering."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import sys
from collections.abc import AsyncIterable, Callable, Iterable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import msgspec

_logger = logging.getLogger("laminite")

_STREAM_SENTINEL = object()
# Shared pool for sync stream next()/close(); keep bounded under concurrency.
_STREAM_EXECUTOR = ThreadPoolExecutor(max_workers=32, thread_name_prefix="laminite-stream")

# Iterator close jobs run on their own pool: the stream pool can be saturated by
# blocked next() calls (long-lived sync generators), which must not delay close()
# and with it request-scoped provider cleanup.
_STREAM_CLOSE_EXECUTOR = ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="laminite-stream-close"
)

CONTENT_TYPE_JSON = (b"content-type", b"application/json")
CONTENT_TYPE_TEXT = (b"content-type", b"text/plain; charset=utf-8")
CONTENT_TYPE_OCTET = (b"content-type", b"application/octet-stream")

# One encoder for every body, so success and error responses encode identically.
_ENCODER = msgspec.json.Encoder()

# Prevent header-injection: names must be RFC 9110 tokens; values must be
# RFC 9110 field-content (VCHAR / SP / HTAB), so reject every other CTL.
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_HEADER_VALUE_INVALID_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


def validate_header_name(name: str) -> None:
    if not _HEADER_NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid header name: {name!r}")


def validate_header_value(value: str) -> None:
    if _HEADER_VALUE_INVALID_RE.search(value):
        raise ValueError(f"Invalid header value: {value!r}")
    try:
        value.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise ValueError(f"Invalid header value: {value!r}") from exc


def validate_header_pair(name: str, value: str) -> None:
    validate_header_name(name)
    validate_header_value(value)


def encode_header_pair(name: str, value: str) -> tuple[bytes, bytes]:
    validate_header_pair(name, value)
    return name.lower().encode("latin-1"), value.encode("latin-1")


def _append_headers(
    raw_headers: list[tuple[bytes, bytes]],
    headers: Mapping[str, str] | None,
    media_type: str | None,
) -> None:
    if headers:
        for key, value in headers.items():
            raw_headers.append(encode_header_pair(key, value))
    if media_type is not None:
        raw_headers.append(encode_header_pair("content-type", media_type))


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
    # Framing is ours to set; a caller-supplied Content-Length or Transfer-Encoding
    # could disagree with the body (RFC 9112 forbids sending both together).
    header_list = [
        pair for pair in headers if pair[0] not in (b"content-length", b"transfer-encoding")
    ]
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
    try:
        if hasattr(content, "__aiter__"):
            iterator: Any = aiter(content)  # type: ignore[arg-type]
            is_async = True
        else:
            iterator = iter(content)  # type: ignore[arg-type]
            is_async = False
    except Exception:
        # Iterator construction failed before any chunk could be produced; emit a
        # 500 so the client sees a response instead of an empty ASGI reply.
        status, headers, body = error_json("Internal Server Error", 500)
        with contextlib.suppress(Exception):
            await send_response(
                send, status_code=status, headers=headers, body=body, is_head=is_head
            )
        raise

    loop = asyncio.get_running_loop()
    pending_cf: Future[Any] | None = None

    async def run_sync(fn: Any, *args: Any) -> Any:
        nonlocal pending_cf
        cf = _STREAM_EXECUTOR.submit(fn, *args)
        pending_cf = cf
        try:
            return await asyncio.wrap_future(cf, loop=loop)
        finally:
            # Keep pending_cf if cancelled while the worker is still running.
            if cf.done():
                pending_cf = None

    try:
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": list(response.raw_headers),
            }
        )

        if is_head:
            # HEAD must not advance the body; the outer finally handles close.
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return

        try:
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
        except BaseException:
            # The response already started and cannot be replaced; terminate it
            # so servers do not abort the connection with an incomplete message.
            with contextlib.suppress(BaseException):
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            raise
        await send({"type": "http.response.body", "body": b"", "more_body": False})
    finally:
        # Cancellation does not stop a worker next(). Temporarily uncancel so
        # wait+close both complete before cancellation is restored.
        task = asyncio.current_task()
        cancel_count = task.cancelling() if task is not None else 0
        for _ in range(cancel_count):
            assert task is not None
            task.uncancel()
        try:
            if pending_cf is not None and not pending_cf.done():
                # Must not shadow the primary exception; close still has to run.
                with contextlib.suppress(BaseException):
                    await asyncio.wrap_future(pending_cf, loop=loop)
            try:
                if is_async:
                    await _force_close_async_iterator(iterator)
                else:
                    await loop.run_in_executor(_STREAM_CLOSE_EXECUTOR, _sync_close, iterator)
            except BaseException as close_exc:
                _logger.warning("Stream iterator close failed", exc_info=close_exc)
        finally:
            if task is not None and not isinstance(sys.exc_info()[1], asyncio.CancelledError):
                # Re-arm only when the stream is not already unwinding with
                # CancelledError: a fresh cancellation would abort the caller's
                # deferred provider teardown.
                for _ in range(cancel_count):
                    task.cancel()


def error_json(detail: Any, status_code: int) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    return _render_json({"detail": detail}, status_code)
