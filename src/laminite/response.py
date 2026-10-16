"""Response types and return-value rendering."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import msgspec

CONTENT_TYPE_JSON = (b"content-type", b"application/json")
CONTENT_TYPE_TEXT = (b"content-type", b"text/plain; charset=utf-8")
CONTENT_TYPE_OCTET = (b"content-type", b"application/octet-stream")

_ENCODER = msgspec.json.Encoder()


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
        if headers:
            for key, value in headers.items():
                self.raw_headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))
        if media_type is not None:
            self.raw_headers.append((b"content-type", media_type.encode("latin-1")))


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


def render_result(
    result: Any,
    *,
    status_code: int,
    encoder: msgspec.json.Encoder = _ENCODER,
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    """Render a handler return value to status, headers, body."""
    result_type: type[Any] = type(result)
    renderer = _RENDERERS.get(result_type)
    if renderer is not None:
        return renderer(result, status_code, encoder)
    if isinstance(result, Response):
        return _render_response(result, status_code, encoder)
    return _render_json(result, status_code, encoder)


def _render_response(
    result: Response,
    _status_code: int,
    _encoder: msgspec.json.Encoder,
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    headers = list(result.raw_headers)
    return result.status_code, headers, result.body


def _render_none(
    _result: None,
    status_code: int,
    _encoder: msgspec.json.Encoder,
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    return status_code, [], b""


def _render_bytes(
    result: bytes,
    status_code: int,
    _encoder: msgspec.json.Encoder,
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    return status_code, [CONTENT_TYPE_OCTET], result


def _render_str(
    result: str,
    status_code: int,
    _encoder: msgspec.json.Encoder,
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    return status_code, [CONTENT_TYPE_TEXT], result.encode("utf-8")


def _render_json(
    result: Any,
    status_code: int,
    encoder: msgspec.json.Encoder,
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    return status_code, [CONTENT_TYPE_JSON], encoder.encode(result)


_Renderer = Callable[
    [Any, int, msgspec.json.Encoder],
    tuple[int, list[tuple[bytes, bytes]], bytes],
]

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


def error_json(detail: Any, status_code: int) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    return _render_json({"detail": detail}, status_code, _ENCODER)
