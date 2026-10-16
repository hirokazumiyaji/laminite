from typing import Annotated

import pytest
from msgspec import Struct

from laminite import HTTPException, Laminite, Query, Request
from laminite.testing import TestClient


def test_chapter1_example_without_body() -> None:
    app = Laminite()

    @app.get("/")
    def read_root() -> dict[str, str]:
        return {"message": "Hello from Laminite"}

    @app.get("/items/{item_id}")
    async def read_item(item_id: int, q: str | None = None) -> dict[str, object]:
        return {"item_id": item_id, "q": q}

    client = TestClient(app)
    assert client.get("/").json() == {"message": "Hello from Laminite"}
    assert client.get("/items/1", query={"q": "x"}).json() == {"item_id": 1, "q": "x"}
    assert client.get("/items/1").json() == {"item_id": 1, "q": None}
    bad = client.get("/items/abc")
    assert bad.status_code == 422
    assert "detail" in bad.json()
    client.close()


def test_unhandled_exception_is_500() -> None:
    app = Laminite()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret")

    client = TestClient(app)
    res = client.get("/boom")
    assert res.status_code == 500
    assert res.json() == {"detail": "Internal Server Error"}
    assert "secret" not in res.text
    client.close()


def test_freeze_blocks_registration() -> None:
    app = Laminite()

    @app.get("/")
    def root() -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app)
    client.get("/")
    with pytest.raises(RuntimeError):
        app.get("/x")(lambda: None)
    with pytest.raises(RuntimeError):
        app.add_middleware(lambda app: app)
    client.close()


def test_rejected_handler_at_freeze() -> None:
    app = Laminite()

    @app.get("/bad")
    def bad(*args: object) -> None:  # type: ignore[no-untyped-def]
        return None

    client = TestClient(app)
    with pytest.raises(TypeError):
        client.get("/bad")
    client.close()


def test_http_exception() -> None:
    app = Laminite()

    @app.get("/gone")
    def gone() -> None:
        raise HTTPException(404)

    client = TestClient(app)
    res = client.get("/gone")
    assert res.status_code == 404
    assert res.json() == {"detail": "Not Found"}
    client.close()


def test_405_allow_header() -> None:
    app = Laminite()

    @app.get("/only-get")
    def only_get() -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app)
    res = client.post("/only-get")
    assert res.status_code == 405
    assert "GET" in res.headers.get("allow", "")
    assert "HEAD" in res.headers.get("allow", "")
    client.close()


def test_head_request() -> None:
    app = Laminite()

    @app.get("/text")
    def text() -> str:
        return "hello"

    client = TestClient(app)
    res = client.head("/text")
    assert res.status_code == 200
    assert res.content == b""
    assert res.headers["content-length"] == "5"
    client.close()


def test_status_code_none_is_204() -> None:
    app = Laminite()

    @app.delete("/x")
    def delete_x() -> None:
        return None

    client = TestClient(app)
    assert client.delete("/x").status_code == 204
    client.close()


def test_query_and_header_binding() -> None:
    from laminite import Header

    app = Laminite()

    @app.get("/search")
    def search(
        q: str,
        page_size: Annotated[int, Query(alias="page-size")] = 20,
        flag: bool = False,
        ratios: list[int] | None = None,
        request_id: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return {
            "q": q,
            "page_size": page_size,
            "flag": flag,
            "ratios": ratios,
            "request_id": request_id,
        }

    client = TestClient(app)
    res = client.get(
        "/search",
        query={"q": "foo", "page-size": "10", "flag": "true", "ratios": ["1", "2"]},
        headers={"request-id": "abc"},
    )
    assert res.status_code == 200
    assert res.json() == {
        "q": "foo",
        "page_size": 10,
        "flag": True,
        "ratios": [1, 2],
        "request_id": "abc",
    }
    missing = client.get("/search")
    assert missing.status_code == 422
    client.close()


def test_no_query_collection_without_params() -> None:
    from laminite.compiler import RouteInfo, analyze_handler
    from laminite.di import DependencyRegistry
    from laminite.router import path_parameter_names

    app = Laminite()

    @app.get("/plain")
    def plain() -> dict[str, str]:
        return {"ok": "1"}

    info = RouteInfo(
        path="/plain",
        methods=("GET",),
        handler=plain,
        status_code=None,
        offload=False,
        path_param_names=path_parameter_names("/plain"),
    )
    compiled = analyze_handler(info, DependencyRegistry())
    assert compiled.needs_query is False
    assert compiled.needs_headers is False
    assert compiled.params_type is None


class Item(Struct):
    name: str
    price: float
    is_offer: bool = False


def test_body_shared_with_request() -> None:
    app = Laminite()

    @app.post("/items")
    async def create(request: Request, body: Item) -> dict[str, object]:
        raw = await request.body()
        return {"name": body.name, "raw_len": len(raw)}

    client = TestClient(app)
    res = client.post("/items", json={"name": "x", "price": 1.0})
    assert res.status_code == 200
    assert res.json()["name"] == "x"
    assert res.json()["raw_len"] > 0
    client.close()


def test_response_status_overrides_route_status() -> None:
    """Design 11.3: returning Response ignores the route status_code."""
    from laminite import JSONResponse

    app = Laminite()

    @app.post("/items", status_code=201)
    def create() -> JSONResponse:
        return JSONResponse({"ok": True}, status_code=202)

    client = TestClient(app)
    assert client.post("/items").status_code == 202
    client.close()


def test_body_decode() -> None:
    app = Laminite()

    @app.post("/items", status_code=201)
    async def create_item(body: Item) -> dict[str, object]:
        return {"name": body.name, "total": body.price * 1.1}

    client = TestClient(app)
    res = client.post("/items", json={"name": "x", "price": 10.0})
    assert res.status_code == 201
    assert res.json()["name"] == "x"
    bad_json = client.post("/items", content=b"{")
    assert bad_json.status_code == 400
    bad_schema = client.post("/items", json={"name": "x"})
    assert bad_schema.status_code == 422
    client.close()


def test_body_list_with_marker() -> None:
    from typing import Annotated

    from laminite import Body

    app = Laminite()

    @app.post("/bulk")
    async def bulk(items: Annotated[list[Item], Body()]) -> dict[str, int]:
        return {"count": len(items)}

    client = TestClient(app)
    res = client.post("/bulk", json=[{"name": "a", "price": 1.0}, {"name": "b", "price": 2.0}])
    assert res.status_code == 200
    assert res.json() == {"count": 2}
    client.close()


def test_request_state() -> None:
    app = Laminite()

    class StateMiddleware:
        def __init__(self, app: object) -> None:
            self.app = app

        async def __call__(self, scope: dict, receive: object, send: object) -> None:
            if scope["type"] == "http":
                scope.setdefault("state", {})
                scope["state"]["from_mw"] = "yes"
            await self.app(scope, receive, send)  # type: ignore[operator]

    app.add_middleware(StateMiddleware)

    @app.get("/")
    def root(request: Request) -> dict[str, object]:
        return {"v": request.state.get("from_mw")}

    with TestClient(app) as client:
        assert client.get("/").json() == {"v": "yes"}
