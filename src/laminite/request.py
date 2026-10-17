"""ASGI Request wrapper."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, TypeVar
from urllib.parse import parse_qsl

import msgspec

T = TypeVar("T")


class Headers(Mapping[str, str]):
    __slots__ = ("_multi", "_pairs", "_store")

    def __init__(self, headers: Sequence[tuple[bytes, bytes]]) -> None:
        store: dict[str, str] = {}
        for key, value in headers:
            store[key.decode("latin-1").lower()] = value.decode("latin-1")
        self._store = store
        self._pairs = tuple(headers)
        self._multi: dict[str, list[str]] | None = None

    def __getitem__(self, key: str) -> str:
        return self._store[key.lower()]

    def __iter__(self) -> Iterator[str]:
        return iter(self._store)

    def __len__(self) -> int:
        return len(self._store)

    def get(self, key: str, default: str | None = None) -> str | None:  # type: ignore[override]
        return self._store.get(key.lower(), default)

    def getlist(self, key: str) -> list[str]:
        multi = self._multi
        if multi is None:
            built: dict[str, list[str]] = {}
            for key_bytes, value in self._pairs:
                built.setdefault(key_bytes.decode("latin-1").lower(), []).append(
                    value.decode("latin-1")
                )
            multi = built
            self._multi = built
        return list(multi.get(key.lower(), []))


def parse_query_pairs(query_string: bytes, max_num_fields: int | None) -> list[tuple[str, str]]:
    try:
        return parse_qsl(
            query_string.decode("latin-1"),
            keep_blank_values=True,
            max_num_fields=max_num_fields,
        )
    except ValueError as exc:
        from laminite.exceptions import HTTPException

        raise HTTPException(400, "Too many query fields") from exc


class QueryParams(Mapping[str, str]):
    __slots__ = ("_items", "_multi")

    def __init__(self, query_string: bytes, *, max_num_fields: int | None = None) -> None:
        pairs = parse_query_pairs(query_string, max_num_fields)
        multi: dict[str, list[str]] = {}
        for key, value in pairs:
            multi.setdefault(key, []).append(value)
        self._multi = multi
        self._items = {key: values[-1] for key, values in multi.items()}

    def __getitem__(self, key: str) -> str:
        return self._items[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def getlist(self, key: str) -> list[str]:
        return list(self._multi.get(key, []))


class Request:
    __slots__ = (
        "_body",
        "_cookies",
        "_headers",
        "_max_body_size",
        "_max_query_fields",
        "_path_params",
        "_query_params",
        "_receive",
        "scope",
    )

    ROUTE_SCOPE_KEY = "laminite.route"
    BODY_SCOPE_KEY = "laminite.body"

    def __init__(
        self,
        scope: dict[str, Any],
        receive: Any,
        *,
        path_params: Mapping[str, str] | None = None,
        max_body_size: int = 1_048_576,
        max_query_fields: int | None = 1000,
    ) -> None:
        self.scope = scope
        self._receive = receive
        self._path_params: Mapping[str, str] = path_params if path_params is not None else {}
        self._headers: Headers | None = None
        self._query_params: QueryParams | None = None
        self._cookies: dict[str, str] | None = None
        self._body: bytes | None = None
        self._max_body_size = max_body_size
        self._max_query_fields = max_query_fields

    @property
    def method(self) -> str:
        return self.scope["method"]  # type: ignore[no-any-return]

    @property
    def path(self) -> str:
        return self.scope["path"]  # type: ignore[no-any-return]

    @property
    def route(self) -> str | None:
        """Matched path template, or ``None`` when no route matched (404/405)."""
        return self.scope.get(self.ROUTE_SCOPE_KEY)  # type: ignore[no-any-return]

    @property
    def path_params(self) -> Mapping[str, str]:
        return self._path_params

    @property
    def headers(self) -> Headers:
        if self._headers is None:
            self._headers = Headers(self.scope.get("headers", []))
        return self._headers

    @property
    def query_params(self) -> QueryParams:
        if self._query_params is None:
            self._query_params = QueryParams(
                self.scope.get("query_string", b""),
                max_num_fields=self._max_query_fields,
            )
        return self._query_params

    @property
    def cookies(self) -> dict[str, str]:
        if self._cookies is None:
            self._cookies = parse_cookie_header("; ".join(self.headers.getlist("cookie")))
        return self._cookies

    @property
    def state(self) -> dict[str, Any]:
        state = self.scope.get("state")
        if state is None:
            state = {}
            self.scope["state"] = state
        return state  # type: ignore[no-any-return]

    async def body(self) -> bytes:
        if self._body is None:
            cached = self.scope.get(self.BODY_SCOPE_KEY)
            if cached is not None:
                self._body = cached
                return cached  # type: ignore[no-any-return]
            _reject_oversized_content_length(self.scope.get("headers", []), self._max_body_size)
            self._body = await _receive_body(self._receive, self._max_body_size)
        return self._body

    def _cache_body(self, body: bytes) -> None:
        """Record a body already read by the request pipeline."""
        self._body = body
        self.scope[self.BODY_SCOPE_KEY] = body

    async def json(self, tp: type[T] | Any = Any) -> T:
        data = await self.body()
        if tp is Any:
            return msgspec.json.decode(data)  # type: ignore[no-any-return]
        return msgspec.json.decode(data, type=tp)


_CONTENT_LENGTH_RE = re.compile(r"^[0-9]+$")


def inspect_body_headers(headers: Sequence[tuple[bytes, bytes]], max_body_size: int) -> str | None:
    """Return Content-Type while validating Content-Length in one pass."""
    from laminite.exceptions import HTTPException

    content_type: str | None = None
    seen: int | None = None
    for key, value in headers:
        if key == b"content-length":
            raw = value.decode("latin-1").strip()
            if not _CONTENT_LENGTH_RE.fullmatch(raw):
                raise HTTPException(400, "Invalid Content-Length")
            length = int(raw)
            if seen is not None and length != seen:
                raise HTTPException(400, "Invalid Content-Length")
            seen = length
        elif key == b"content-type":
            content_type = value.decode("latin-1")
    if seen is not None and seen > max_body_size:
        raise HTTPException(413, "Payload Too Large")
    return content_type


def _reject_oversized_content_length(
    headers: Sequence[tuple[bytes, bytes]], max_body_size: int
) -> None:
    inspect_body_headers(headers, max_body_size)


def is_json_content_type(content_type: str | None) -> bool:
    if content_type is None:
        return False
    media = content_type.split(";", 1)[0].strip().lower()
    if media == "application/json":
        return True
    prefix, suffix = "application/", "+json"
    return (
        media.startswith(prefix)
        and media.endswith(suffix)
        and len(media) > len(prefix) + len(suffix)
    )


def parse_cookie_header(header: str) -> dict[str, str]:
    """Parse one or more merged Cookie header fields into a name→value map.

    The Cookie request header does not share the Set-Cookie grammar, so it is
    parsed directly: ``http.cookies.SimpleCookie`` would consume reserved names
    such as ``path`` or ``expires`` as attributes of the previous cookie.
    """
    cookies: dict[str, str] = {}
    for chunk in header.split(";"):
        name, sep, value = chunk.strip().partition("=")
        if not sep or not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        cookies[name] = value
    return cookies


async def _receive_body(receive: Any, max_body_size: int) -> bytes:
    from laminite.exceptions import HTTPException

    total = 0
    buf: bytearray | None = None
    first: bytes | None = None

    while True:
        message = await receive()
        message_type = message["type"]
        if message_type == "http.disconnect":
            raise _Disconnect()
        if message_type != "http.request":
            continue
        chunk: bytes = message.get("body", b"")
        more = message.get("more_body", False)
        total += len(chunk)
        if total > max_body_size:
            raise HTTPException(413, "Payload Too Large")
        if first is None and not more:
            return chunk
        if first is None:
            first = chunk
            if more:
                buf = bytearray(chunk)
            else:
                return chunk
        else:
            assert buf is not None
            buf.extend(chunk)
        if not more:
            assert buf is not None
            return bytes(buf)


class _Disconnect(Exception):
    """Client disconnected while reading the body."""
