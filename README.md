# Laminite

A minimal ASGI web framework for Python. Startup-time compilation, msgspec validation, and a thin request path.

## Requirements

- Python 3.12+

## Install

```bash
pip install laminite
```

Runtime dependency: `msgspec` only.

## Quick start

```python
import laminite
from msgspec import Struct

app = laminite.Laminite()


class Item(Struct):
    name: str
    price: float
    is_offer: bool = False


@app.get("/")
def read_root():
    return {"message": "Hello from Laminite"}


@app.get("/items/{item_id}")
async def read_item(item_id: int, q: str | None = None):
    return {"item_id": item_id, "q": q}


@app.post("/items", status_code=201)
async def create_item(body: Item):
    return {"name": body.name, "total": body.price * 1.1}
```

Run with any ASGI server, for example:

```bash
granian --interface asgi module:app
```

## Features

- Static map + segment-trie routing (404 / 405 / HEAD→GET)
- `APIRouter` sub-routers, flattened into the app at include time
- Typed path, query, header, and cookie binding via msgspec, including `msgspec.Meta`
- `{name:path}` wildcard path parameters and multi-value headers
- JSON body decode with `msgspec.Struct` or `Annotated[..., Body()]`
- `Response`, `JSONResponse`, and `StreamingResponse` (SSE-friendly) with `set_cookie`
- OpenAPI 3.1 document generation at freeze time (`openapi_url=...`)
- Type-keyed dependency injection (app / request scope)
- Lifespan, ASGI middleware, exception handlers, and an unhandled-exception hook
- Route templates exposed on the request scope for instrumentation
- Zero-dependency `TestClient`

## OpenTelemetry

[`laminite-opentelemetry`](packages/laminite-opentelemetry/README.md) records OpenTelemetry spans
for requests:

```bash
pip install laminite-opentelemetry
```

```python
from laminite_opentelemetry import instrument_app

instrument_app(app)
```

## Dependency policy

`[project.dependencies]` contains only `msgspec`. Dev and benchmark tools live in uv dependency groups and are not installed with the package.

## Documentation

- [Design](docs/design.md)
- [User guide](docs/guide/index.md): routing and `APIRouter`, parameters and cookies, responses
  and streaming, dependencies, OpenAPI, error handling, testing
- [Benchmarks](docs/benchmarks.md)
- [Changelog](CHANGELOG.md)

## License

MIT
