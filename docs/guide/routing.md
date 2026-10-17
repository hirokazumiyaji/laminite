# Routing

Path templates start with `/`. Parameters use `{name}` and must occupy a whole segment. `{name}` must be a valid Python identifier. Partial segments such as `/files/{name}.json` are rejected.

Trailing slashes are significant: `/items` and `/items/` are different paths. Laminite does not redirect.

When a path matches but the method does not, Laminite returns 405 with an `Allow` header. If `GET` is registered, `HEAD` is included in `Allow`.

`HEAD` requests fall back to the `GET` handler. The response keeps the real `content-length` and sends an empty body.

## Sub-routers

Split routes across modules with `APIRouter`, then flatten them into the app with `include_router`. Prefixes are applied at registration time, so request dispatch cost stays the same as a single router.

```python
from laminite import APIRouter, Laminite

items = APIRouter()


@items.get("/{item_id}")
async def read_item(item_id: int):
    return {"item_id": item_id}


v1 = APIRouter(prefix="/v1")
v1.include_router(items)

app = Laminite()
app.include_router(v1)  # GET /v1/{item_id}
app.include_router(items, prefix="/legacy")  # GET /legacy/{item_id}
```

Calling `include_router` after the app is frozen raises `RuntimeError`.
