"""APIRouter / include_router flattening (#25)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from msgspec import Struct

from laminite import APIRouter, Laminite
from laminite.testing import TestClient


class Item(Struct):
    name: str


@dataclass
class Svc:
    name: str


def test_include_router_with_prefix() -> None:
    items = APIRouter()

    @items.get("/{item_id}")
    def read_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    app = Laminite()
    app.include_router(items, prefix="/v1")

    client = TestClient(app)
    assert client.get("/v1/1").json() == {"item_id": 1}
    assert client.get("/1").status_code == 404
    client.close()


def test_nested_include_routers() -> None:
    items = APIRouter()

    @items.get("/{item_id}")
    def read_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    v1 = APIRouter(prefix="/v1")
    v1.include_router(items)

    api = APIRouter(prefix="/api")
    api.include_router(v1)

    app = Laminite()
    app.include_router(api)

    client = TestClient(app)
    assert client.get("/api/v1/42").json() == {"item_id": 42}
    client.close()


def test_include_router_after_freeze_raises() -> None:
    app = Laminite()

    @app.get("/")
    def root() -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app)
    client.get("/")
    client.close()

    extra = APIRouter()

    @extra.get("/x")
    def x() -> dict[str, str]:
        return {"x": "1"}

    with pytest.raises(RuntimeError, match="frozen"):
        app.include_router(extra)


def test_included_routes_support_params_body_and_di() -> None:
    router = APIRouter()

    @router.get("/search")
    def search(q: str, limit: int = 10) -> dict[str, object]:
        return {"q": q, "limit": limit}

    @router.post("/items")
    def create(item: Item, svc: Svc) -> dict[str, str]:
        return {"name": item.name, "svc": svc.name}

    app = Laminite()
    app.provide(Svc, Svc("dep"))
    app.include_router(router, prefix="/api")

    client = TestClient(app)
    assert client.get("/api/search", query={"q": "hi", "limit": "3"}).json() == {
        "q": "hi",
        "limit": 3,
    }
    res = client.post("/api/items", json={"name": "a"})
    assert res.status_code == 200
    assert res.json() == {"name": "a", "svc": "dep"}
    client.close()


def test_router_own_prefix_and_include_prefix_combine() -> None:
    users = APIRouter(prefix="/users")

    @users.get("/{user_id}")
    def read_user(user_id: int) -> dict[str, int]:
        return {"user_id": user_id}

    app = Laminite()
    app.include_router(users, prefix="/v1")

    client = TestClient(app)
    assert client.get("/v1/users/7").json() == {"user_id": 7}
    client.close()


def test_all_http_methods_on_api_router() -> None:
    router = APIRouter()

    @router.put("/r")
    def put_r() -> dict[str, str]:
        return {"m": "PUT"}

    @router.patch("/r")
    def patch_r() -> dict[str, str]:
        return {"m": "PATCH"}

    @router.delete("/r")
    def delete_r() -> dict[str, str]:
        return {"m": "DELETE"}

    @router.post("/r")
    def post_r() -> dict[str, str]:
        return {"m": "POST"}

    app = Laminite()
    app.include_router(router)

    client = TestClient(app)
    assert client.put("/r").json() == {"m": "PUT"}
    assert client.patch("/r").json() == {"m": "PATCH"}
    assert client.delete("/r").json() == {"m": "DELETE"}
    assert client.post("/r").json() == {"m": "POST"}
    client.close()


def test_include_with_prefix_on_nested_include() -> None:
    items = APIRouter()

    @items.get("/")
    def list_items() -> dict[str, str]:
        return {"ok": "1"}

    parent = APIRouter()
    parent.include_router(items, prefix="/items")

    app = Laminite()
    app.include_router(parent, prefix="/api")

    client = TestClient(app)
    assert client.get("/api/items").json() == {"ok": "1"}
    client.close()


def test_prefix_slash_joins_root_path() -> None:
    router = APIRouter(prefix="/")

    @router.get("/")
    def root() -> dict[str, str]:
        return {"root": "1"}

    app = Laminite()
    app.include_router(router)

    client = TestClient(app)
    assert client.get("/").json() == {"root": "1"}
    client.close()


def test_invalid_prefixes_and_paths_raise() -> None:
    with pytest.raises(ValueError, match="Prefix must start"):
        APIRouter(prefix="v1")

    router = APIRouter()
    with pytest.raises(ValueError, match="Path must start"):

        @router.get("items")
        def bad() -> dict[str, str]:
            return {}

    with pytest.raises(ValueError, match="Prefix must start"):
        router.include_router(APIRouter(), prefix="nested")

    app = Laminite()
    with pytest.raises(ValueError, match="Prefix must start"):
        app.include_router(APIRouter(), prefix="bad")


def test_join_paths_validation() -> None:
    from laminite.routing import join_paths

    assert join_paths("", "/a") == "/a"
    assert join_paths("/v1/", "/a") == "/v1/a"
    assert join_paths("/", "/") == "/"
    assert join_paths("/", "/x") == "/x"
    with pytest.raises(ValueError):
        join_paths("", "a")
    with pytest.raises(ValueError):
        join_paths("v1", "/a")
    with pytest.raises(ValueError):
        join_paths("/v1", "a")
