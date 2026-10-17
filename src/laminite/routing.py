"""Composable API routers that flatten into Laminite at include/freeze time."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from laminite.compiler import RouteInfo
from laminite.router import path_parameter_names

F = TypeVar("F", bound=Callable[..., Any])


def _validate_prefix(prefix: str) -> None:
    if prefix != "" and not prefix.startswith("/"):
        raise ValueError(f"Prefix must start with '/': {prefix!r}")


def join_paths(prefix: str, path: str) -> str:
    """Join a router prefix with a route path.

    Both sides must be absolute ASGI paths (start with ``/``). A trailing slash
    on ``prefix`` (except ``/`` itself) is stripped before joining.
    """
    if prefix == "":
        if not path.startswith("/"):
            raise ValueError(f"Path must start with '/': {path!r}")
        return path
    if not prefix.startswith("/"):
        raise ValueError(f"Prefix must start with '/': {prefix!r}")
    if not path.startswith("/"):
        raise ValueError(f"Path must start with '/': {path!r}")

    base = "" if prefix == "/" else prefix.rstrip("/")
    if path == "/":
        return base or "/"
    return f"{base}{path}"


@dataclass(slots=True)
class _PendingRoute:
    path: str
    methods: tuple[str, ...]
    handler: Callable[..., Any]
    status_code: int | None
    offload: bool


@dataclass(slots=True)
class _PendingInclude:
    router: APIRouter
    prefix: str


class APIRouter:
    """A mountable route table. Routes are flattened when included into Laminite."""

    def __init__(self, *, prefix: str = "") -> None:
        _validate_prefix(prefix)
        self.prefix = prefix
        self._routes: list[_PendingRoute] = []
        self._includes: list[_PendingInclude] = []

    def route(
        self,
        path: str,
        methods: Sequence[str],
        *,
        status_code: int | None = None,
        offload: bool = False,
    ) -> Callable[[F], F]:
        def decorator(func: F) -> F:
            if not path.startswith("/"):
                raise ValueError(f"Path must start with '/': {path!r}")
            self._routes.append(
                _PendingRoute(
                    path=path,
                    methods=tuple(m.upper() for m in methods),
                    handler=func,
                    status_code=status_code,
                    offload=offload,
                )
            )
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

    def include_router(self, router: APIRouter, *, prefix: str = "") -> None:
        _validate_prefix(prefix)
        self._includes.append(_PendingInclude(router=router, prefix=prefix))

    def iter_route_infos(self, *, prefix: str = "") -> Iterator[RouteInfo]:
        """Yield flattened RouteInfo values with prefixes applied."""
        base = join_paths(prefix, self.prefix) if self.prefix else prefix
        for pending in self._routes:
            path = join_paths(base, pending.path) if base else pending.path
            yield RouteInfo(
                path=path,
                methods=pending.methods,
                handler=pending.handler,
                status_code=pending.status_code,
                offload=pending.offload,
                path_param_names=path_parameter_names(path),
            )
        for included in self._includes:
            child_base = join_paths(base, included.prefix) if included.prefix else base
            yield from included.router.iter_route_infos(prefix=child_base)
