"""OpenAPI 3.1 freeze-time generation (#28)."""

from __future__ import annotations

from typing import Annotated

import msgspec
from msgspec import Struct

from laminite import Header, Laminite, Query
from laminite.testing import TestClient


class Item(Struct):
    name: str
    qty: int = 1


def test_openapi_disabled_by_default_has_no_route() -> None:
    app = Laminite()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app)
    assert client.get("/openapi.json").status_code == 404
    doc = app.openapi()
    assert doc["openapi"] == "3.1.0"
    assert "/ping" in doc["paths"]
    client.close()


def test_openapi_url_serves_preencoded_schema() -> None:
    app = Laminite(title="Demo", version="1.2.3", openapi_url="/openapi.json")

    @app.get("/items/{item_id}")
    def read_item(
        item_id: int,
        q: str | None = None,
        page_size: Annotated[int, Query(alias="page-size")] = 20,
        request_id: Annotated[str | None, Header()] = None,
    ) -> Item:
        return Item(name="x", qty=item_id)

    @app.post("/items")
    def create_item(item: Item) -> Item:
        return item

    client = TestClient(app)
    res = client.get("/openapi.json")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/json"
    doc = msgspec.json.decode(res.content)
    assert doc["info"] == {"title": "Demo", "version": "1.2.3"}
    assert doc["openapi"] == "3.1.0"

    get_op = doc["paths"]["/items/{item_id}"]["get"]
    param_names = {p["name"] for p in get_op["parameters"]}
    assert "item_id" in param_names
    assert "page-size" in param_names
    assert "request-id" in param_names or "request_id" in param_names

    post_op = doc["paths"]["/items"]["post"]
    assert "requestBody" in post_op
    assert "Item" in doc["components"]["schemas"]

    # Second request must return identical bytes (freeze-time encode once).
    assert client.get("/openapi.json").content == res.content
    client.close()


def test_openapi_method_matches_served_document() -> None:
    app = Laminite(openapi_url="/openapi.json")

    @app.get("/x")
    def x() -> dict[str, int]:
        return {"n": 1}

    client = TestClient(app)
    served = msgspec.json.decode(client.get("/openapi.json").content)
    assert app.openapi() == served
    client.close()


def test_openapi_list_and_none_responses() -> None:
    app = Laminite()

    @app.get("/items", status_code=200)
    def list_items() -> list[Item]:
        return []

    @app.delete("/items/{item_id}", status_code=204)
    def delete_item(item_id: int) -> None:
        return None

    doc = app.openapi()
    assert "200" in doc["paths"]["/items"]["get"]["responses"]
    assert doc["paths"]["/items/{item_id}"]["delete"]["responses"]["204"]["description"] == (
        "No Content"
    )


def test_openapi_nested_struct_refs_resolve_to_components() -> None:
    app = Laminite()

    @app.get("/optional")
    def get_optional() -> Item | None:
        return None

    @app.get("/items")
    def get_items() -> list[Item | None]:
        return []

    @app.get("/map")
    def get_map() -> dict[str, Item]:
        return {}

    doc = app.openapi()
    serialized = msgspec.json.encode(doc).decode()
    assert "#/$defs/" not in serialized
    assert "#/components/schemas/Item" in serialized
    assert "Item" in doc["components"]["schemas"]


def test_openapi_does_not_freeze_app() -> None:
    app = Laminite()

    @app.get("/a")
    def a() -> dict[str, str]:
        return {"a": "1"}

    doc = app.openapi()
    assert "/a" in doc["paths"]
    assert app.frozen is False

    @app.get("/b")
    def b() -> dict[str, str]:
        return {"b": "1"}

    assert "/b" in app.openapi()["paths"]
