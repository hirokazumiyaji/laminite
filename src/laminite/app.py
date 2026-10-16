"""Laminite ASGI application."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, TypeVar

import msgspec

from laminite.compiler import RouteInfo, compile_route, path_param_names
from laminite.di import DependencyRegistry
from laminite.exceptions import HTTPException
from laminite.response import error_json, send_response
from laminite.router import Match, MethodNotAllowed, NotFound, Router

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])
E = TypeVar("E", bound=BaseException)
H = TypeVar("H", bound=Callable[..., Any])

ASGIApp = Callable[..., Any]


class Laminite:
    def __init__(
        self,
        *,
        lifespan: Callable[[Laminite], AbstractAsyncContextManager[None]] | None = None,
        max_body_size: int = 1_048_576,
    ) -> None:
        self._lifespan_factory = lifespan
        self._max_body_size = max_body_size
        self._router: Router[Any] = Router()
        self._routes: list[RouteInfo] = []
        self._registry = DependencyRegistry()
        self._middleware: list[tuple[Callable[..., ASGIApp], dict[str, Any]]] = []
        self._exception_handlers: dict[type, Callable[..., Any]] = {}
        self._frozen = False
        self._encoder = msgspec.json.Encoder()
        self._lifespan_cm: AbstractAsyncContextManager[None] | None = None
        self._overrides: Mapping[type, Any] | None = None
        # Before freeze: ensure + dispatch. After freeze: middleware stack or dispatcher.
        self._http = self._http_before_freeze

    def _ensure_mutable(self) -> None:
        if self._frozen:
            raise RuntimeError("Application is frozen; cannot register further")

    def route(
        self,
        path: str,
        methods: Sequence[str],
        *,
        status_code: int | None = None,
        offload: bool = False,
    ) -> Callable[[F], F]:
        def decorator(func: F) -> F:
            self._add_route(path, methods, func, status_code=status_code, offload=offload)
            return func

        return decorator

    def get(
        self, path: str, *, status_code: int | None = None, offload: bool = False
    ) -> Callable[[F], F]:
        return self.route(path, ["GET"], status_code=status_code, offload=offload)

    def post(
        self, path: str, *, status_code: int | None = None, offload: bool = False
    ) -> Callable[[F], F]:
        return self.route(path, ["POST"], status_code=status_code, offload=offload)

    def put(
        self, path: str, *, status_code: int | None = None, offload: bool = False
    ) -> Callable[[F], F]:
        return self.route(path, ["PUT"], status_code=status_code, offload=offload)

    def patch(
        self, path: str, *, status_code: int | None = None, offload: bool = False
    ) -> Callable[[F], F]:
        return self.route(path, ["PATCH"], status_code=status_code, offload=offload)

    def delete(
        self, path: str, *, status_code: int | None = None, offload: bool = False
    ) -> Callable[[F], F]:
        return self.route(path, ["DELETE"], status_code=status_code, offload=offload)

    def _add_route(
        self,
        path: str,
        methods: Sequence[str],
        handler: Callable[..., Any],
        *,
        status_code: int | None,
        offload: bool,
    ) -> None:
        self._ensure_mutable()
        methods_t = tuple(m.upper() for m in methods)
        info = RouteInfo(
            path=path,
            methods=methods_t,
            handler=handler,
            status_code=status_code,
            offload=offload,
            path_param_names=path_param_names(path),
        )
        self._routes.append(info)
        # Placeholder value; replaced at freeze with pipeline.
        for method in methods_t:
            self._router.add(path, method, info)

    def provide(self, tp: type[T], instance: T) -> None:
        self._ensure_mutable()
        self._registry.provide(tp, instance)

    def provider(self, func: F) -> F:
        self._ensure_mutable()
        return self._registry.provider(func)

    def add_middleware(self, cls: Callable[..., ASGIApp], **options: Any) -> None:
        self._ensure_mutable()
        self._middleware.append((cls, options))

    def exception_handler(self, exc_type: type[E]) -> Callable[[H], H]:
        def decorator(func: H) -> H:
            self._ensure_mutable()
            self._exception_handlers[exc_type] = func
            return func

        return decorator

    @property
    def frozen(self) -> bool:
        return self._frozen

    def set_overrides(self, overrides: Mapping[type, Any] | None) -> None:
        if self._frozen:
            raise RuntimeError("Application is frozen; cannot set overrides")
        self._overrides = overrides

    def freeze(self, *, overrides: Mapping[type, Any] | None = None) -> None:
        if self._frozen:
            return
        if overrides is not None:
            self._overrides = overrides
        # Replace the registry so compilation and any later lookups share overrides.
        self._registry = self._registry.with_overrides(self._overrides)
        pipelines: dict[int, Any] = {}
        new_router: Router[Any] = Router()
        for info in self._routes:
            key = id(info)
            if key not in pipelines:
                pipelines[key] = compile_route(
                    info,
                    self._registry,
                    max_body_size=self._max_body_size,
                    encoder=self._encoder,
                )
            pipeline = pipelines[key]
            for method in info.methods:
                new_router.add(info.path, method, pipeline)
        self._router = new_router

        async def dispatcher(scope: dict[str, Any], receive: Any, send: Any) -> None:
            await self._dispatch(scope, receive, send)

        app: ASGIApp = dispatcher
        for cls, options in reversed(self._middleware):
            app = cls(app, **options)
        self._http = app
        self._frozen = True

    async def _http_before_freeze(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.freeze(overrides=self._overrides)
        await self._http(scope, receive, send)

    async def _dispatch(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if "state" not in scope:
            scope["state"] = {}
        method = scope["method"]
        path = scope["path"]
        try:
            match: Match[Any] = self._router.resolve(method, path)
        except NotFound:
            status, headers, body = error_json("Not Found", 404)
            await send_response(
                send, status_code=status, headers=headers, body=body, is_head=method == "HEAD"
            )
            return
        except MethodNotAllowed as exc:
            allow = ", ".join(sorted(exc.allowed))
            status, headers, body = error_json("Method Not Allowed", 405)
            headers.append((b"allow", allow.encode("latin-1")))
            await send_response(
                send, status_code=status, headers=headers, body=body, is_head=method == "HEAD"
            )
            return

        pipeline = match.value
        await pipeline(
            scope,
            receive,
            send,
            path_params=match.path_params,
            is_head=match.is_head or method == "HEAD",
            exception_handlers=self._exception_handlers,
        )

    async def _lifespan(self, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    if self._lifespan_factory is not None:
                        self._lifespan_cm = self._lifespan_factory(self)
                        await self._lifespan_cm.__aenter__()
                    self.freeze(overrides=self._overrides)
                except Exception as exc:
                    await send(
                        {
                            "type": "lifespan.startup.failed",
                            "message": str(exc),
                        }
                    )
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                try:
                    if self._lifespan_cm is not None:
                        await self._lifespan_cm.__aexit__(None, None, None)
                        self._lifespan_cm = None
                except Exception as exc:
                    await send(
                        {
                            "type": "lifespan.shutdown.failed",
                            "message": str(exc),
                        }
                    )
                    return
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope["type"]
        if scope_type == "http":
            await self._http(scope, receive, send)
        elif scope_type == "lifespan":
            await self._lifespan(receive, send)
        else:
            await send({"type": "websocket.close", "code": 1000})


# Re-export for typing convenience
__all__ = ["HTTPException", "Laminite"]
