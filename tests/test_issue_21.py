"""TestClient overrides on frozen apps and 413 via exception handlers (#21)."""

from __future__ import annotations

from dataclasses import dataclass

from msgspec import Struct

from laminite import HTTPException, Laminite, Request, Response
from laminite.testing import TestClient


@dataclass
class Svc:
    name: str


class Item(Struct):
    name: str


def _app_with_svc() -> Laminite:
    app = Laminite()
    app.provide(Svc, Svc("real"))

    @app.get("/")
    def root(svc: Svc) -> dict[str, str]:
        return {"name": svc.name}

    return app


def test_overrides_work_after_app_already_frozen() -> None:
    app = _app_with_svc()

    with TestClient(app) as client:
        assert client.get("/").json() == {"name": "real"}

    assert app.frozen

    with TestClient(app, overrides={Svc: Svc("fake")}) as client:
        assert client.get("/").json() == {"name": "fake"}


def test_overrides_do_not_leak_to_later_clients() -> None:
    app = _app_with_svc()

    with TestClient(app, overrides={Svc: Svc("fake")}) as client:
        assert client.get("/").json() == {"name": "fake"}

    with TestClient(app) as client:
        assert client.get("/").json() == {"name": "real"}


def test_http_exception_handler_receives_413_from_body() -> None:
    app = Laminite(max_body_size=8)

    @app.exception_handler(HTTPException)
    def on_http(request: Request, exc: HTTPException) -> Response:
        return Response(
            f"handled:{exc.status_code}:{exc.detail}".encode(),
            status_code=exc.status_code,
            media_type="text/plain",
        )

    @app.post("/items")
    async def create(item: Item) -> dict[str, str]:
        return {"name": item.name}

    client = TestClient(app)
    res = client.post("/items", content=b"0123456789")
    assert res.status_code == 413
    assert res.text == "handled:413:Payload Too Large"
    client.close()
