"""Instrument a Laminite app with OpenTelemetry ASGI middleware."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from opentelemetry import trace
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.trace import Status, StatusCode

from laminite.app import Laminite
from laminite.request import Request

ASGIApp = Callable[..., Any]


class _RouteSpanEnricher:
    """Update the active span with ``http.route`` once Laminite has matched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        async def send_wrapper(message: dict[str, Any]) -> None:
            if scope.get("type") == "http" and message.get("type") == "http.response.start":
                span = trace.get_current_span()
                if span.is_recording():
                    route = scope.get(Request.ROUTE_SCOPE_KEY)
                    method = scope.get("method", "")
                    if isinstance(route, str) and route:
                        span.update_name(f"{method} {route}")
                        span.set_attribute("http.route", route)
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _otel_middleware(
    app: ASGIApp,
    *,
    tracer_provider: Any | None = None,
    meter_provider: Any | None = None,
    **options: Any,
) -> ASGIApp:
    enricher = _RouteSpanEnricher(app)
    options.setdefault("exclude_spans", ["receive", "send"])
    return OpenTelemetryMiddleware(
        enricher,
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
    if getattr(app, "_laminite_otel_instrumented", False):
        return app

    previous = app._on_unhandled_exception

    def on_unhandled(request: Request | dict[str, Any], exc: BaseException) -> None:
        span = trace.get_current_span()
        if span.is_recording():
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
        if previous is not None:
            previous(request, exc)

    app._on_unhandled_exception = on_unhandled
    app.add_middleware(
        _otel_middleware,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        **options,
    )
    app._laminite_otel_instrumented = True  # type: ignore[attr-defined]
    return app
