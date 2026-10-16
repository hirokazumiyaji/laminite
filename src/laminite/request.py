"""ASGI Request wrapper."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any, TypeVar
from urllib.parse import parse_qsl

import msgspec

T = TypeVar("T")


class Headers(Mapping[str, str]):
    __slots__ = ("_store",)

    def __init__(self, headers: Sequence[tuple[bytes, bytes]]) -> None:
        store: dict[str, str] = {}
        for key, value in headers:
            store[key.decode("latin-1").lower()] = value.decode("latin-1")
        self._store = store

    def __getitem__(self, key: str) -> str:
        return self._store[key.lower()]

    def __iter__(self) -> Iterator[str]:
        return iter(self._store)

    def __len__(self) -> int:
        return len(self._store)

    def get(self, key: str, default: str | None = None) -> str | None:  # type: ignore[override]
        return self._store.get(key.lower(), default)


class QueryParams(Mapping[str, str]):
    __slots__ = ("_items", "_multi")

    def __init__(self, query_string: bytes) -> None:
        pairs = parse_qsl(query_string.decode("latin-1"), keep_blank_values=True)
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
        "_headers",
        "_max_body_size",
        "_path_params",
        "_query_params",
        "_receive",
        "scope",
    )

    def __init__(
        self,
        scope: dict[str, Any],
        receive: Any,
        *,
        path_params: dict[str, str] | None = None,
        max_body_size: int = 1_048_576,
    ) -> None:
        self.scope = scope
        self._receive = receive
        self._path_params = path_params if path_params is not None else {}
        self._headers: Headers | None = None
        self._query_params: QueryParams | None = None
        self._body: bytes | None = None
        self._max_body_size = max_body_size

    @property
    def method(self) -> str:
        return self.scope["method"]  # type: ignore[no-any-return]

    @property
    def path(self) -> str:
        return self.scope["path"]  # type: ignore[no-any-return]

    @property
    def path_params(self) -> dict[str, str]:
        return self._path_params

    @property
    def headers(self) -> Headers:
        if self._headers is None:
            self._headers = Headers(self.scope.get("headers", []))
        return self._headers

    @property
    def query_params(self) -> QueryParams:
        if self._query_params is None:
            self._query_params = QueryParams(self.scope.get("query_string", b""))
        return self._query_params

    @property
    def state(self) -> dict[str, Any]:
        state = self.scope.get("state")
        if state is None:
            state = {}
            self.scope["state"] = state
        return state  # type: ignore[no-any-return]

    async def body(self) -> bytes:
        if self._body is None:
            self._body = await _receive_body(self._receive, self._max_body_size)
        return self._body

    async def json(self, tp: type[T] | Any = Any) -> T:
        data = await self.body()
        if tp is Any:
            return msgspec.json.decode(data)  # type: ignore[no-any-return]
        return msgspec.json.decode(data, type=tp)


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
