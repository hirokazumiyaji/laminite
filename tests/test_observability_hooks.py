"""Route templates on scope and on_unhandled_exception (#29)."""

from __future__ import annotations

from laminite import HTTPException, Laminite, Request
from laminite.testing import TestClient


def test_request_route_template_on_match() -> None:
    app = Laminite()
    seen: list[str | None] = []

    @app.get("/items/{item_id}")
    def read_item(item_id: int, request: Request) -> dict[str, object]:
        seen.append(request.route)
        return {"item_id": item_id, "route": request.route}

    client = TestClient(app)
    res = client.get("/items/7")
    assert res.json() == {"item_id": 7, "route": "/items/{item_id}"}
    assert seen == ["/items/{item_id}"]
    client.close()


def test_scope_route_visible_to_middleware_after_handler() -> None:
    captured: list[tuple[bool, str | None]] = []

    class Capture:
        def __init__(self, app):  # type: ignore[no-untyped-def]
            self.app = app

        async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
            await self.app(scope, receive, send)
            if scope["type"] == "http":
                key = Request.ROUTE_SCOPE_KEY
                captured.append((key in scope, scope.get(key)))

    app = Laminite()
    app.add_middleware(Capture)

    @app.get("/x/{name}")
    def x(name: str) -> dict[str, str]:
        return {"name": name}

    client = TestClient(app)
    client.get("/x/a")
    client.get("/missing")
    client.close()
    assert captured[0] == (True, "/x/{name}")
    assert captured[1] == (False, None)


def test_405_does_not_set_route_template() -> None:
    app = Laminite()

    @app.get("/only-get")
    def only_get() -> dict[str, str]:
        return {"ok": "1"}

    class Capture:
        def __init__(self, app):  # type: ignore[no-untyped-def]
            self.app = app

        async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
            await self.app(scope, receive, send)
            if scope["type"] == "http":
                assert Request.ROUTE_SCOPE_KEY not in scope

    app.add_middleware(Capture)
    client = TestClient(app)
    assert client.post("/only-get").status_code == 405
    client.close()


def test_on_unhandled_exception_hook() -> None:
    seen: list[tuple[str | None, BaseException]] = []

    def on_exc(request: Request, exc: BaseException) -> None:
        seen.append((request.route, exc))

    app = Laminite(on_unhandled_exception=on_exc)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("fail")

    client = TestClient(app)
    assert client.get("/boom").status_code == 500
    assert len(seen) == 1
    route, exc = seen[0]
    assert route == "/boom"
    assert isinstance(exc, RuntimeError)
    client.close()


def test_on_unhandled_exception_async_hook() -> None:
    seen: list[BaseException] = []

    async def on_exc(request: Request, exc: BaseException) -> None:
        seen.append(exc)

    app = Laminite(on_unhandled_exception=on_exc)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("fail")

    client = TestClient(app)
    assert client.get("/boom").status_code == 500
    assert len(seen) == 1
    assert isinstance(seen[0], RuntimeError)
    client.close()


def test_http_exception_does_not_trigger_hook() -> None:
    seen: list[BaseException] = []

    app = Laminite(on_unhandled_exception=lambda _r, e: seen.append(e))

    @app.get("/gone")
    def gone() -> None:
        raise HTTPException(404, "gone")

    client = TestClient(app)
    assert client.get("/gone").status_code == 404
    assert seen == []
    client.close()


def test_handled_exception_does_not_trigger_hook() -> None:
    seen: list[BaseException] = []

    app = Laminite(on_unhandled_exception=lambda _r, e: seen.append(e))

    class Known(Exception):
        pass

    @app.exception_handler(Known)
    def on_known(request: Request, exc: Known) -> dict[str, str]:
        return {"ok": "1"}

    @app.get("/known")
    def known() -> None:
        raise Known()

    client = TestClient(app)
    assert client.get("/known").json() == {"ok": "1"}
    assert seen == []
    client.close()


def test_hook_failure_still_returns_500() -> None:
    invoked: list[bool] = []

    def bad_hook(request: Request, exc: BaseException) -> None:
        invoked.append(True)
        raise RuntimeError("hook failed")

    app = Laminite(on_unhandled_exception=bad_hook)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("fail")

    client = TestClient(app)
    assert client.get("/boom").status_code == 500
    assert invoked == [True]
    client.close()


def test_exception_handler_failure_triggers_hook() -> None:
    seen: list[BaseException] = []

    app = Laminite(on_unhandled_exception=lambda _r, e: seen.append(e))

    class Known(Exception):
        pass

    @app.exception_handler(Known)
    def on_known(request: Request, exc: Known) -> dict[str, str]:
        raise RuntimeError("handler boom")

    @app.get("/known")
    def known() -> None:
        raise Known()

    client = TestClient(app)
    assert client.get("/known").status_code == 500
    assert len(seen) == 1
    assert isinstance(seen[0], RuntimeError)
    client.close()
