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
- Typed path, query, and header binding via msgspec
- JSON body decode with `msgspec.Struct` or `Annotated[..., Body()]`
- Type-keyed dependency injection (app / request scope)
- Lifespan, ASGI middleware, exception handlers
- Zero-dependency `TestClient`

## Dependency policy

`[project.dependencies]` contains only `msgspec`. Dev and benchmark tools live in uv dependency groups and are not installed with the package.

## Documentation

- [Design](docs/design.md)
- [User guide](docs/guide/index.md)
- [Benchmarks](docs/benchmarks.md)

## License

MIT
