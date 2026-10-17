"""Laminite ASGI application."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, TypeVar

from laminite.compiler import RouteInfo, compile_route
from laminite.di import DependencyRegistry
from laminite.exceptions import HTTPException
from laminite.response import error_json, send_response
from laminite.router import Match, MethodNotAllowed, NotFound, Router, path_parameter_names
from laminite.routing import APIRouter, _validate_prefix
from laminite.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("laminite")

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])
E = TypeVar("E", bound=BaseException)
H = TypeVar("H", bound=Callable[..., Any])


class Laminite:
    def __init__(
        self,
        *,
        lifespan: Callable[[Laminite], AbstractAsyncContextManager[None]] | None = None,
        max_body_size: int = 1_048_576,
        max_query_fields: int = 1000,
    ) -> None:
        self._lifespan_factory = lifespan
        self._max_body_size = max_body_size
        self._max_query_fields = max_query_fields
        self._router: Router[Any] = Router()
        self._routes: list[RouteInfo] = []
        self._registry = DependencyRegistry()
        self._middleware: list[tuple[Callable[..., ASGIApp], dict[str, Any]]] = []
        self._exception_handlers: dict[type, Callable[..., Any]] = {}
        self._frozen = False
        self._lifespan_cm: AbstractAsyncContextManager[None] | None = None
        self._overrides: Mapping[type, Any] | None = None
        self._http: ASGIApp = self._http_before_freeze

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
        max_body_size: int | None = None,
    ) -> Callable[[F], F]:
        def decorator(func: F) -> F:
            self._add_route(
                path,
                methods,
                func,
                status_code=status_code,
                offload=offload,
                max_body_size=max_body_size,
            )
            return func

        return decorator

    def get(
        self,
        path: str,
        *,
        status_code: int | None = None,
        offload: bool = False,
        max_body_size: int | None = None,
    ) -> Callable[[F], F]:
        return self.route(
            path, ["GET"], status_code=status_code, offload=offload, max_body_size=max_body_size
        )

    def post(
        self,
        path: str,
        *,
        status_code: int | None = None,
        offload: bool = False,
        max_body_size: int | None = None,
    ) -> Callable[[F], F]:
        return self.route(
            path, ["POST"], status_code=status_code, offload=offload, max_body_size=max_body_size
        )

    def put(
        self,
        path: str,
        *,
        status_code: int | None = None,
        offload: bool = False,
        max_body_size: int | None = None,
    ) -> Callable[[F], F]:
        return self.route(
            path, ["PUT"], status_code=status_code, offload=offload, max_body_size=max_body_size
        )

    def patch(
        self,
        path: str,
        *,
        status_code: int | None = None,
        offload: bool = False,
        max_body_size: int | None = None,
    ) -> Callable[[F], F]:
        return self.route(
            path, ["PATCH"], status_code=status_code, offload=offload, max_body_size=max_body_size
        )

    def delete(
        self,
        path: str,
        *,
        status_code: int | None = None,
        offload: bool = False,
        max_body_size: int | None = None,
    ) -> Callable[[F], F]:
        return self.route(
            path, ["DELETE"], status_code=status_code, offload=offload, max_body_size=max_body_size
        )

    def _add_route(
        self,
        path: str,
        methods: Sequence[str],
        handler: Callable[..., Any],
        *,
        status_code: int | None,
        offload: bool,
        max_body_size: int | None,
    ) -> None:
        self._ensure_mutable()
        info = RouteInfo(
            path=path,
            methods=tuple(m.upper() for m in methods),
            handler=handler,
            status_code=status_code,
            offload=offload,
            path_param_names=path_parameter_names(path),
            max_body_size=max_body_size,
        )
        self._add_route_info(info)

    def _add_route_info(self, info: RouteInfo) -> None:
        self._ensure_mutable()
        self._routes.append(info)
        # Registration-time validation only (duplicate routes, param-name
        # conflicts); dispatch uses the frozen table built in _build_http_asgi.
        for method in info.methods:
            self._router.add(info.path, method, info)

    def provide(self, tp: type[T], instance: T) -> None:
        self._ensure_mutable()
        self._registry.provide(tp, instance)

    def provider(self, func: F) -> F:
        self._ensure_mutable()
        return self._registry.provider(func)

    def add_middleware(self, cls: Callable[..., ASGIApp], **options: Any) -> None:
        self._ensure_mutable()
        self._middleware.append((cls, options))

    def include_router(self, router: APIRouter, *, prefix: str = "") -> None:
        """Flatten ``router`` into this app. No per-request mount overhead after freeze."""
        self._ensure_mutable()
        _validate_prefix(prefix)
        for info in router.iter_route_infos(prefix=prefix):
            self._add_route_info(info)

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
        """Store overrides applied on the next ``freeze`` (pre-freeze only).

        Prefer ``TestClient(..., overrides=...)`` / ``http_with_overrides`` so
        overrides do not rewrite the base registry or leak across tests.
        """
        if self._frozen:
            raise RuntimeError("Application is frozen; cannot set overrides")
        self._overrides = overrides

    def freeze(self, *, overrides: Mapping[type, Any] | None = None) -> None:
        if self._frozen:
            return
        if overrides is not None:
            self._overrides = overrides
        # Build the primary ASGI app from an override-applied registry while
        # keeping the base registry intact so http_with_overrides can diverge per client.
        self._http = self._build_http_asgi(self._registry.with_overrides(self._overrides))
        self._frozen = True

    def _merge_unresolvable_overrides(self, overrides: Mapping[type, Any] | None) -> None:
        """Fold override types the base registry cannot resolve into ``_overrides``.

        Freeze-time dependency analysis must recognize types that only a
        TestClient's overrides supply, or route compilation fails with
        ``Unregistered dependency``. Registered types stay per-client via
        ``http_with_overrides``; only unresolvable types are folded into the
        shared app, because no other binding exists for them at freeze time.
        """
        if self._frozen or not overrides:
            return
        current = dict(self._overrides or {})
        changed = False
        for tp, instance in overrides.items():
            if tp not in current and not self._registry.has(tp):
                current[tp] = instance
                changed = True
        if changed:
            self._overrides = current

    def http_with_overrides(self, overrides: Mapping[type, Any]) -> ASGIApp:
        """Return an HTTP ASGI app with dependency overrides.

        The base registry is left untouched; only override types with no
        binding at all are folded into a first freeze (see
        ``_merge_unresolvable_overrides``).
        """
        if not self._frozen:
            self._merge_unresolvable_overrides(overrides)
            self.freeze()
        return self._build_http_asgi(
            self._registry.with_overrides(self._overrides).with_overrides(overrides)
        )

    def _build_http_asgi(self, registry: DependencyRegistry) -> ASGIApp:
        pipelines: dict[int, Any] = {}
        router: Router[Any] = Router()
        for info in self._routes:
            key = id(info)
            if key not in pipelines:
                pipelines[key] = compile_route(
                    info,
                    registry,
                    max_body_size=self._max_body_size,
                    max_query_fields=self._max_query_fields,
                )
            pipeline = pipelines[key]
            for method in info.methods:
                router.add(info.path, method, pipeline)

        async def dispatcher(scope: Scope, receive: Receive, send: Send) -> None:
            await self._dispatch(scope, receive, send, router, self._exception_handlers)

        app: ASGIApp = dispatcher
        for cls, options in reversed(self._middleware):
            app = cls(app, **options)
        return app

    async def _http_before_freeze(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.freeze(overrides=self._overrides)
        await self._http(scope, receive, send)

    async def _dispatch(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        router: Router[Any],
        exception_handlers: Mapping[type, Callable[..., Any]],
    ) -> None:
        method = scope["method"]
        path = scope["path"]
        try:
            match: Match[Any] = router.resolve(method, path)
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
            exception_handlers=exception_handlers,
        )

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    if self._lifespan_factory is not None:
                        self._lifespan_cm = self._lifespan_factory(self)
                        await self._lifespan_cm.__aenter__()
                    self.freeze(overrides=self._overrides)
                except Exception as exc:
                    if self._lifespan_cm is not None:
                        try:
                            await self._lifespan_cm.__aexit__(type(exc), exc, exc.__traceback__)
                        except Exception:
                            logger.exception("Lifespan cleanup after startup failure raised")
                        finally:
                            self._lifespan_cm = None
                    await send(
                        {
                            "type": "lifespan.startup.failed",
                            "message": str(exc),
                        }
                    )
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                cm = self._lifespan_cm
                self._lifespan_cm = None
                try:
                    if cm is not None:
                        await cm.__aexit__(None, None, None)
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

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope["type"]
        if scope_type == "http":
            await self._http(scope, receive, send)
        elif scope_type == "lifespan":
            await self._lifespan(receive, send)
        elif scope_type == "websocket":
            await send({"type": "websocket.close", "code": 1000})
        else:
            raise RuntimeError(f"Unsupported ASGI scope type: {scope_type!r}")


__all__ = ["HTTPException", "Laminite"]
