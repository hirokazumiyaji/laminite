"""Synchronous TestClient without external HTTP dependencies."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar
from urllib.parse import urlencode

import msgspec

from laminite.app import Laminite
from laminite.types import Message

T = TypeVar("T")


class TestResponse:
    def __init__(self, status_code: int, headers: dict[str, str], content: bytes) -> None:
        self.status_code = status_code
        self.headers = headers
        self.content = content

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def json(self, tp: type[T] | Any = Any) -> T:
        if tp is Any:
            return msgspec.json.decode(self.content)  # type: ignore[no-any-return]
        return msgspec.json.decode(self.content, type=tp)


class TestClient:
    __test__ = False

    def __init__(
        self,
        app: Laminite,
        *,
        overrides: Mapping[type, Any] | None = None,
    ) -> None:
        self.app = app
        self.overrides = overrides
        self._runner = asyncio.Runner()
        self._lifespan_task: asyncio.Task[None] | None = None
        self._receive_q: asyncio.Queue[Message] | None = None
        self._send_q: asyncio.Queue[Message] | None = None
        self._entered = False
        self._http_app: Any | None = None

    def __enter__(self) -> TestClient:
        self._entered = True
        self._receive_q = asyncio.Queue()
        self._send_q = asyncio.Queue()

        # Make override-only types visible to the freeze-time dependency
        # analysis that lifespan startup runs on the shared app.
        self.app._merge_unresolvable_overrides(self.overrides)

        async def lifespan_call() -> None:
            assert self._receive_q is not None and self._send_q is not None

            receive_q = self._receive_q
            send_q = self._send_q

            async def receive() -> Message:
                return await receive_q.get()

            async def send(message: Message) -> None:
                await send_q.put(message)

            # Lifespan freezes the shared app without baking overrides into it.
            await self.app({"type": "lifespan"}, receive, send)

        self._lifespan_task = self._runner.get_loop().create_task(lifespan_call())
        self._runner.run(self._startup())
        return self

    async def _startup(self) -> None:
        assert self._receive_q is not None and self._send_q is not None
        await self._receive_q.put({"type": "lifespan.startup"})
        message = await self._send_q.get()
        if message["type"] != "lifespan.startup.complete":
            raise RuntimeError(f"Lifespan startup failed: {message}")

    def __exit__(self, *args: object) -> None:
        if self._entered:
            self._runner.run(self._shutdown())
        self._runner.close()

    async def _shutdown(self) -> None:
        assert self._receive_q is not None and self._send_q is not None
        await self._receive_q.put({"type": "lifespan.shutdown"})
        message = await self._send_q.get()
        if message["type"] != "lifespan.shutdown.complete":
            raise RuntimeError(f"Lifespan shutdown failed: {message}")
        if self._lifespan_task is not None:
            await self._lifespan_task

    def close(self) -> None:
        if not self._entered:
            self._runner.close()

    def _resolve_http_app(self) -> Any:
        if self._http_app is not None:
            return self._http_app
        if not self.overrides:
            self._http_app = self.app
        else:
            self._http_app = self.app.http_with_overrides(self.overrides)
        return self._http_app

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str | Sequence[str]] | None = None,
        headers: Mapping[str, str] | None = None,
        json: Any = None,
        content: bytes | None = None,
    ) -> TestResponse:
        return self._runner.run(
            self._request_async(
                method, path, query=query, headers=headers, json=json, content=content
            )
        )

    async def _request_async(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str | Sequence[str]] | None,
        headers: Mapping[str, str] | None,
        json: Any,
        content: bytes | None,
    ) -> TestResponse:
        query_string = b""
        if query:
            pairs: list[tuple[str, str]] = []
            for key, value in query.items():
                if isinstance(value, str):
                    pairs.append((key, value))
                elif isinstance(value, bytes):
                    pairs.append((key, value.decode()))
                else:
                    for item in value:
                        pairs.append((key, item))
            query_string = urlencode(pairs).encode("latin-1")

        header_list: list[tuple[bytes, bytes]] = []
        body = b""
        if json is not None:
            body = msgspec.json.encode(json)
            header_list.append((b"content-type", b"application/json"))
        elif content is not None:
            body = content

        if headers:
            for key, value in headers.items():
                header_list.append((key.lower().encode("latin-1"), value.encode("latin-1")))

        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method.upper(),
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": query_string,
            "headers": header_list,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "state": {},
        }

        messages: list[Message] = []
        body_sent = False

        async def receive() -> Message:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            messages.append(message)

        await self._resolve_http_app()(scope, receive, send)

        status = 500
        resp_headers: dict[str, str] = {}
        resp_body = b""
        for message in messages:
            if message["type"] == "http.response.start":
                status = message["status"]
                resp_headers = {
                    k.decode("latin-1"): v.decode("latin-1") for k, v in message.get("headers", [])
                }
            elif message["type"] == "http.response.body":
                resp_body += message.get("body", b"")
        return TestResponse(status, resp_headers, resp_body)

    def get(self, path: str, **kwargs: Any) -> TestResponse:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> TestResponse:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> TestResponse:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> TestResponse:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> TestResponse:
        return self.request("DELETE", path, **kwargs)

    def head(self, path: str, **kwargs: Any) -> TestResponse:
        return self.request("HEAD", path, **kwargs)
