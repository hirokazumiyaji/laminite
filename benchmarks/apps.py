"""Comparable ASGI apps for microbenchmark scenarios S1-S5."""

from typing import Any

from msgspec import Struct
from pydantic import BaseModel


class Item(Struct):
    name: str
    price: float
    is_offer: bool = False
    tags: list[str] = []
    note: str = "x" * 80


class ItemModel(BaseModel):
    name: str
    price: float
    is_offer: bool = False
    tags: list[str] = []
    note: str = "x" * 80


def build_laminite() -> Any:
    from laminite import Laminite

    app = Laminite()

    @app.get("/")
    def s1() -> dict[str, str]:
        return {"message": "ok"}

    @app.get("/items/{item_id}")
    def s2(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @app.get("/search")
    def s3(q: str, limit: int = 10) -> dict[str, object]:
        return {"q": q, "limit": limit}

    @app.post("/items")
    def s4(body: Item) -> dict[str, object]:
        return {"name": body.name, "price": body.price}

    return app


def build_starlette() -> Any:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def s1(_request: Request) -> JSONResponse:
        return JSONResponse({"message": "ok"})

    async def s2(request: Request) -> JSONResponse:
        return JSONResponse({"item_id": int(request.path_params["item_id"])})

    async def s3(request: Request) -> JSONResponse:
        q = request.query_params.get("q", "")
        limit = int(request.query_params.get("limit", "10"))
        return JSONResponse({"q": q, "limit": limit})

    async def s4(request: Request) -> JSONResponse:
        data = await request.json()
        return JSONResponse({"name": data["name"], "price": data["price"]})

    return Starlette(
        routes=[
            Route("/", s1),
            Route("/items/{item_id:int}", s2),
            Route("/search", s3),
            Route("/items", s4, methods=["POST"]),
        ]
    )


def build_fastapi() -> Any:
    from fastapi import FastAPI, Query

    app = FastAPI()

    @app.get("/")
    async def s1() -> dict[str, str]:
        return {"message": "ok"}

    @app.get("/items/{item_id}")
    async def s2(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @app.get("/search")
    async def s3(q: str, limit: int = Query(10)) -> dict[str, object]:
        return {"q": q, "limit": limit}

    @app.post("/items")
    async def s4(body: ItemModel) -> dict[str, object]:
        return {"name": body.name, "price": body.price}

    return app


def build_litestar() -> Any:
    from litestar import Litestar, get, post

    @get("/")
    async def s1() -> dict[str, str]:
        return {"message": "ok"}

    @get("/items/{item_id:int}")
    async def s2(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @get("/search")
    async def s3(q: str, limit: int = 10) -> dict[str, object]:
        return {"q": q, "limit": limit}

    @post("/items")
    async def s4(data: ItemModel) -> dict[str, object]:
        return {"name": data.name, "price": data.price}

    return Litestar(route_handlers=[s1, s2, s3, s4])


SCENARIOS: dict[str, tuple[str, str, bytes]] = {
    "S1": ("GET", "/", b""),
    "S2": ("GET", "/items/42", b""),
    "S3": ("GET", "/search?q=foo&limit=10", b""),
    "S4": (
        "POST",
        "/items",
        (
            b'{"name":"widget","price":9.99,"is_offer":false,'
            b'"tags":["a","b"],"note":"' + b"x" * 80 + b'"}'
        ),
    ),
    "S5": ("GET", "/missing", b""),
}
