"""Executable snippets from the user guide / README."""

from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from msgspec import Struct

from laminite import Body, Header, Laminite, Query, Request
from laminite.testing import TestClient


class Item(Struct):
    name: str
    price: float
    is_offer: bool = False


def test_readme_quickstart() -> None:
    app = Laminite()

    @app.get("/")
    def read_root() -> dict[str, str]:
        return {"message": "Hello from Laminite"}

    @app.get("/items/{item_id}")
    async def read_item(item_id: int, q: str | None = None) -> dict[str, object]:
        return {"item_id": item_id, "q": q}

    @app.post("/items", status_code=201)
    async def create_item(body: Item) -> dict[str, object]:
        return {"name": body.name, "total": body.price * 1.1}

    client = TestClient(app)
    assert client.get("/").json()["message"] == "Hello from Laminite"
    assert client.get("/items/1", query={"q": "x"}).json() == {"item_id": 1, "q": "x"}
    assert client.post("/items", json={"name": "a", "price": 10}).status_code == 201
    client.close()


def test_guide_parameters_and_di() -> None:
    @dataclass
    class Pool:
        name: str

    @dataclass
    class Conn:
        pool: Pool

    @dataclass
    class User:
        name: str

    @asynccontextmanager
    async def lifespan(app: Laminite):
        app.provide(Pool, Pool("main"))
        yield

    app = Laminite(lifespan=lifespan)

    @app.provider
    def connection(pool: Pool) -> Iterator[Conn]:
        yield Conn(pool)

    @app.provider
    def current_user(request: Request) -> User:
        return User(request.headers.get("authorization") or "anon")

    @app.get("/items/{item_id}")
    async def read_item(
        item_id: int,
        q: str | None = None,
        page_size: Annotated[int, Query(alias="page-size")] = 20,
        request_id: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return {
            "item_id": item_id,
            "q": q,
            "page_size": page_size,
            "request_id": request_id,
        }

    @app.post("/bulk")
    async def bulk(items: Annotated[list[Item], Body()]) -> dict[str, int]:
        return {"count": len(items)}

    @app.get("/me")
    def me(user: User, conn: Conn) -> dict[str, str]:
        return {"user": user.name, "pool": conn.pool.name}

    with TestClient(app) as client:
        assert client.get("/items/1", query={"page-size": "5"}).json()["page_size"] == 5
        assert client.post("/bulk", json=[{"name": "a", "price": 1}]).json() == {"count": 1}
        assert client.get("/me", headers={"authorization": "alice"}).json() == {
            "user": "alice",
            "pool": "main",
        }
