"""InMemorySpanExporter tests for laminite-opentelemetry (#30)."""

from __future__ import annotations

from importlib.metadata import version

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import laminite_opentelemetry
from laminite import Laminite
from laminite.testing import TestClient
from laminite_opentelemetry import instrument_app


def test_version_is_resolved_from_metadata() -> None:
    assert laminite_opentelemetry.__version__ == version("laminite-opentelemetry")


def _setup_tracer() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_span_name_and_http_route_for_matched_path() -> None:
    provider, exporter = _setup_tracer()
    app = Laminite()

    @app.get("/items/{item_id}")
    def read_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    instrument_app(app, tracer_provider=provider)

    client = TestClient(app)
    assert client.get("/items/123").json() == {"item_id": 123}
    client.close()

    spans = exporter.get_finished_spans()
    server = [s for s in spans if s.kind.name == "SERVER"]
    assert server, spans
    span = server[-1]
    assert span.name == "GET /items/{item_id}"
    assert span.attributes is not None
    assert span.attributes.get("http.route") == "/items/{item_id}"


def test_unhandled_exception_recorded_on_span() -> None:
    provider, exporter = _setup_tracer()
    app = Laminite()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("fail")

    instrument_app(app, tracer_provider=provider)

    client = TestClient(app)
    assert client.get("/boom").status_code == 500
    client.close()

    spans = exporter.get_finished_spans()
    assert spans
    span = spans[-1]
    assert span.status.status_code == StatusCode.ERROR
    assert any(e.name == "exception" for e in span.events)


def test_traceparent_propagation() -> None:
    provider, exporter = _setup_tracer()
    app = Laminite()

    @app.get("/")
    def root() -> dict[str, str]:
        return {"ok": "1"}

    instrument_app(app, tracer_provider=provider)

    # Valid W3C traceparent (version-traceid-spanid-flags)
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    client = TestClient(app)
    assert client.get("/", headers={"traceparent": traceparent}).status_code == 200
    client.close()

    spans = exporter.get_finished_spans()
    assert spans
    span = spans[-1]
    assert span.context is not None
    assert format(span.context.trace_id, "032x") == "4bf92f3577b34da6a3ce929d0e0e4736"


def test_instrument_after_freeze_raises() -> None:
    app = Laminite()

    @app.get("/")
    def root() -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app)
    client.get("/")
    client.close()

    with pytest.raises(RuntimeError):
        instrument_app(app)


def test_instrument_app_is_idempotent() -> None:
    provider, exporter = _setup_tracer()
    app = Laminite()

    @app.get("/")
    def root() -> dict[str, str]:
        return {"ok": "1"}

    instrument_app(app, tracer_provider=provider)
    instrument_app(app, tracer_provider=provider)

    client = TestClient(app)
    assert client.get("/").status_code == 200
    client.close()

    server = [s for s in exporter.get_finished_spans() if s.name.startswith("GET")]
    assert len(server) == 1


def test_otel_is_outermost_when_other_middleware_exists() -> None:
    """Existing middleware must run inside the active server span."""
    provider, exporter = _setup_tracer()
    app = Laminite()
    seen_recording: list[bool] = []

    class Capture:
        def __init__(self, app: object) -> None:
            self.app = app

        async def __call__(self, scope: dict, receive: object, send: object) -> None:
            from opentelemetry import trace

            span = trace.get_current_span()
            seen_recording.append(span.is_recording())
            await self.app(scope, receive, send)  # type: ignore[operator]

    @app.get("/")
    def root() -> dict[str, str]:
        return {"ok": "1"}

    app.add_middleware(Capture)
    instrument_app(app, tracer_provider=provider)

    client = TestClient(app)
    assert client.get("/").status_code == 200
    client.close()

    assert seen_recording == [True]
    assert exporter.get_finished_spans()


def test_instrument_app_awaits_async_previous_hook() -> None:
    """A user-supplied async on_unhandled_exception must still be awaited after instrument_app."""
    provider, _ = _setup_tracer()
    seen: list[BaseException] = []

    async def previous(request: object, exc: BaseException) -> None:
        seen.append(exc)

    app = Laminite(on_unhandled_exception=previous)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("fail")

    instrument_app(app, tracer_provider=provider)

    client = TestClient(app)
    assert client.get("/boom").status_code == 500
    client.close()

    assert len(seen) == 1
    assert isinstance(seen[0], RuntimeError)
