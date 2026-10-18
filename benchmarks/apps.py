"""Comparable ASGI apps for the microbenchmark scenarios."""

from dataclasses import dataclass
from typing import Annotated, Any

import msgspec
from msgspec import Struct
from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class Scenario:
    """One request shape measured against every supported target."""

    method: str
    target: str
    body: bytes = b""
    headers: tuple[tuple[bytes, bytes], ...] = ()


@dataclass(frozen=True, slots=True)
class BenchApp:
    """A built ASGI app plus the scenarios it implements."""

    app: Any
    scenarios: frozenset[str]


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


BODY = (
    b'{"name":"widget","price":9.99,"is_offer":false,"tags":["a","b"],"note":"' + b"x" * 80 + b'"}'
)

SCENARIOS: dict[str, Scenario] = {
    "S1": Scenario("GET", "/"),
    "S2": Scenario("GET", "/items/42"),
    "S3": Scenario("GET", "/search?q=foo&limit=10"),
    "S4": Scenario("POST", "/items", body=BODY),
    "S5": Scenario("GET", "/missing"),
    # Feature-path scenarios (v0.2.0): DI provider, typed header/cookie
    # validation, and the {name:path} wildcard route.
    "S6": Scenario("GET", "/di"),
    "S7": Scenario(
        "GET",
        "/secure",
        headers=((b"x-count", b"3"), (b"cookie", b"sid=abc")),
    ),
    "S8": Scenario("GET", "/files/a/b/c.txt"),
}

# Scenarios that the relative-to-Starlette gate evaluates.
BASE_SCENARIOS = ("S1", "S2", "S3", "S4", "S5")


class Svc:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


def _register_base_routes(app: Any) -> None:
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


def build_laminite_base() -> BenchApp:
    """S1-S5 only; usable against older refs that lack v0.2 route APIs."""
    from laminite import Laminite

    app = Laminite()
    _register_base_routes(app)
    return BenchApp(app, frozenset(BASE_SCENARIOS))


def build_laminite() -> BenchApp:
    from laminite import Cookie, Header, Laminite

    app = Laminite()
    _register_base_routes(app)

    @app.provider
    def svc() -> Svc:
        return Svc("bench")

    @app.get("/di")
    def s6(dep: Svc) -> dict[str, str]:
        return {"name": dep.name}

    @app.get("/secure")
    def s7(
        count: Annotated[int, Header(alias="x-count"), msgspec.Meta(ge=1)],
        sid: Annotated[str, Cookie(alias="sid")],
    ) -> dict[str, object]:
        return {"count": count, "sid": sid}

    @app.get("/files/{file_path:path}")
    def s8(file_path: str) -> dict[str, str]:
        return {"file_path": file_path}

    return BenchApp(app, frozenset(SCENARIOS))


def build_starlette() -> BenchApp:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    service = Svc("bench")

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

    async def s6(_request: Request) -> JSONResponse:
        return JSONResponse({"name": service.name})

    async def s7(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "count": int(request.headers.get("x-count", "0")),
                "sid": request.cookies.get("sid", ""),
            }
        )

    async def s8(request: Request) -> JSONResponse:
        return JSONResponse({"file_path": request.path_params["file_path"]})

    return BenchApp(
        Starlette(
            routes=[
                Route("/", s1),
                Route("/items/{item_id:int}", s2),
                Route("/search", s3),
                Route("/items", s4, methods=["POST"]),
                Route("/di", s6),
                Route("/secure", s7),
                Route("/files/{file_path:path}", s8),
            ]
        ),
        frozenset(SCENARIOS),
    )


def build_fastapi() -> BenchApp:
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

    return BenchApp(app, frozenset(BASE_SCENARIOS))


def build_litestar() -> BenchApp:
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

    return BenchApp(Litestar(route_handlers=[s1, s2, s3, s4]), frozenset(BASE_SCENARIOS))
