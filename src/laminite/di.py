"""Type-keyed dependency injection."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Annotated, Any, TypeVar, get_args, get_origin, get_type_hints

from laminite.params import Body, Header, Query
from laminite.request import Request

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])


@dataclass(slots=True, frozen=True)
class ProviderSpec:
    key: type
    func: Callable[..., Any]
    kind: str  # "sync" | "async" | "sync_cm" | "async_cm"
    params: tuple[tuple[str, type], ...]
    needs_request: bool


class DependencyRegistry:
    def __init__(self) -> None:
        self._app: dict[type, Any] = {}
        self._providers: dict[type, ProviderSpec] = {}

    def provide(self, tp: type[T], instance: T) -> None:
        if tp in self._app or tp in self._providers:
            raise ValueError(f"Dependency already registered: {tp!r}")
        self._app[tp] = instance

    def provider(self, func: F) -> F:
        spec = _build_provider_spec(func)
        if spec.key in self._app or spec.key in self._providers:
            raise ValueError(f"Dependency already registered: {spec.key!r}")
        self._providers[spec.key] = spec
        return func

    def has(self, tp: type) -> bool:
        return tp in self._app or tp in self._providers

    def app_values(self) -> Mapping[type, Any]:
        return self._app

    def providers(self) -> Mapping[type, ProviderSpec]:
        return self._providers

    def with_overrides(self, overrides: Mapping[type, Any] | None) -> DependencyRegistry:
        if not overrides:
            return self
        clone = DependencyRegistry()
        clone._app = dict(self._app)
        clone._providers = dict(self._providers)
        for tp, instance in overrides.items():
            clone._providers.pop(tp, None)
            clone._app[tp] = instance
        return clone

    def resolve_order(self, required: set[type]) -> list[ProviderSpec]:
        """Return request-scoped providers in topological order for the required set."""
        visiting: set[type] = set()
        visited: set[type] = set()
        order: list[ProviderSpec] = []

        def visit(tp: type) -> None:
            if tp in self._app:
                return
            if tp in visited:
                return
            if tp in visiting:
                raise ValueError(f"Circular dependency involving {tp!r}")
            spec = self._providers.get(tp)
            if spec is None:
                raise ValueError(f"Unregistered dependency: {tp!r}")
            visiting.add(tp)
            for _, dep in spec.params:
                if dep is not Request:
                    visit(dep)
            visiting.remove(tp)
            visited.add(tp)
            order.append(spec)

        for tp in sorted(required, key=lambda t: t.__qualname__):
            visit(tp)
        return order


def _build_provider_spec(func: Callable[..., Any]) -> ProviderSpec:
    hints = get_type_hints(func, include_extras=True)
    ret = hints.get("return")
    if ret is None:
        raise TypeError(f"Provider {func!r} must have a return type annotation")

    kind: str
    key: type
    wrapped: Callable[..., Any]

    if inspect.isasyncgenfunction(func):
        key = _unwrap_iterator_type(ret)
        wrapped = asynccontextmanager(func)  # type: ignore[arg-type]
        kind = "async_cm"
    elif inspect.isgeneratorfunction(func):
        key = _unwrap_iterator_type(ret)
        wrapped = contextmanager(func)  # type: ignore[arg-type]
        kind = "sync_cm"
    elif inspect.iscoroutinefunction(func):
        key = ret
        wrapped = func
        kind = "async"
    else:
        key = ret
        wrapped = func
        kind = "sync"

    sig = inspect.signature(func)
    params: list[tuple[str, type]] = []
    for name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
        ):
            raise TypeError(f"Unsupported parameter kind in provider {func!r}: {name}")
        ann = strip_annotated(hints.get(name, str))
        params.append((name, ann))

    return ProviderSpec(
        key=key,  # type: ignore[arg-type]
        func=wrapped,
        kind=kind,
        params=tuple(params),
        needs_request=any(tp is Request for _, tp in params),
    )


def strip_annotated(ann: Any) -> Any:
    if get_origin(ann) is Annotated:
        return get_args(ann)[0]
    return ann


def strip_param_markers(ann: Any) -> Any:
    """Remove Laminite Query/Header/Body markers; keep msgspec.Meta and other metadata."""
    if get_origin(ann) is not Annotated:
        return ann
    args = get_args(ann)
    base = args[0]
    kept = tuple(m for m in args[1:] if not isinstance(m, (Query, Header, Body)))
    if not kept:
        return base
    return Annotated[base, *kept]


def _unwrap_iterator_type(ret: Any) -> type:
    origin = get_origin(ret)
    args = get_args(ret)
    if origin in (Iterator, AsyncIterator) or (
        origin is not None
        and getattr(origin, "__name__", "")
        in {
            "Iterator",
            "AsyncIterator",
            "Generator",
            "AsyncGenerator",
        }
    ):
        if not args:
            raise TypeError(f"Cannot determine dependency type from {ret!r}")
        return args[0]  # type: ignore[no-any-return]
    raise TypeError(f"Generator provider must return Iterator[T] or AsyncIterator[T], got {ret!r}")


async def run_providers(
    specs: list[ProviderSpec],
    *,
    app_values: Mapping[type, Any],
    request: Request | None,
    stack: Any,
) -> dict[type, Any]:
    values: dict[type, Any] = dict(app_values)
    if request is not None:
        values[Request] = request
    for spec in specs:
        kwargs = {name: values[tp] for name, tp in spec.params}
        if spec.kind == "sync":
            values[spec.key] = spec.func(**kwargs)
        elif spec.kind == "async":
            values[spec.key] = await spec.func(**kwargs)
        elif spec.kind == "sync_cm":
            cm = spec.func(**kwargs)
            values[spec.key] = stack.enter_context(cm)
        elif spec.kind == "async_cm":
            cm: AbstractAsyncContextManager[Any] = spec.func(**kwargs)
            values[spec.key] = await stack.enter_async_context(cm)
        else:
            raise RuntimeError(f"Unknown provider kind: {spec.kind}")
    return values
