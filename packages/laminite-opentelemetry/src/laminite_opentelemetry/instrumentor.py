"""Instrument a Laminite app with OpenTelemetry ASGI middleware."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from opentelemetry import trace
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.trace import Status, StatusCode

from laminite.app import Laminite
from laminite.request import Request

ASGIApp = Callable[..., Any]


class _SpanSendWrapper:
    """Stamp ``http.route`` on the active span when the response starts."""

    __slots__ = ("scope", "send")

    def __init__(self, scope: dict[str, Any], send: Any) -> None:
        self.scope = scope
        self.send = send

    async def __call__(self, message: dict[str, Any]) -> None:
        if message.get("type") == "http.response.start":
            span = trace.get_current_span()
            if span.is_recording():
                route = self.scope.get(Request.ROUTE_SCOPE_KEY)
                if isinstance(route, str) and route:
                    span.update_name(f"{self.scope['method']} {route}")
                    span.set_attribute("http.route", route)
        await self.send(message)


class _RouteSpanEnricher:
    """Update the active span with ``http.route`` once Laminite has matched."""

    __slots__ = ("app",)

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        await self.app(scope, receive, _SpanSendWrapper(scope, send))


def _otel_middleware(
    app: ASGIApp,
    *,
    tracer_provider: Any | None = None,
    meter_provider: Any | None = None,
    **options: Any,
) -> ASGIApp:
    options.setdefault("exclude_spans", ["receive", "send"])
    return OpenTelemetryMiddleware(
        _RouteSpanEnricher(app),
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        **options,
    )


def instrument_app(
    app: Laminite,
    *,
    tracer_provider: Any | None = None,
    meter_provider: Any | None = None,
    **options: Any,
) -> Laminite:
    """Attach OpenTelemetry ASGI middleware and an unhandled-exception hook.

    Must be called before the app is frozen. Sets span names to
    ``{method} {route}`` when a route matches, records unexpected exceptions,
    and disables per-receive/send child spans by default. Idempotent: a second
    call is a no-op.
    """
    if app.frozen:
        raise RuntimeError("instrument_app must be called before the app is frozen")
    if any(factory is _otel_middleware for factory, _ in app._middleware):
        return app

    previous = app._on_unhandled_exception

    async def on_unhandled(request: Request, exc: BaseException) -> None:
        span = trace.get_current_span()
        if span.is_recording():
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
        if previous is not None:
            result = previous(request, exc)
            if inspect.isawaitable(result):
                await result

    app._on_unhandled_exception = on_unhandled
    # Insert at front so OTEL is outermost after freeze's reversed wrap.
    app._middleware.insert(
        0,
        (
            _otel_middleware,
            {"tracer_provider": tracer_provider, "meter_provider": meter_provider, **options},
        ),
    )
    return app
