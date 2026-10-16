# Parameters

Argument sources are decided at freeze time, in this order:

1. Path template name → path
2. Type is `Request` → request
3. `Annotated` metadata `Header` / `Query` / `Body` → that source
4. Type registered as a dependency → dependency
5. `msgspec.Struct` subclass → body
6. Otherwise → query

```python
from typing import Annotated

from laminite import Body, Header, Laminite, Query
from msgspec import Struct

app = Laminite()


class Item(Struct):
    name: str


@app.get("/items/{item_id}")
async def read_item(
    item_id: int,
    q: str | None = None,
    page_size: Annotated[int, Query(alias="page-size")] = 20,
    request_id: Annotated[str | None, Header()] = None,
):
    return {"item_id": item_id, "q": q, "page_size": page_size, "request_id": request_id}


@app.post("/bulk")
async def bulk(items: Annotated[list[Item], Body()]):
    return {"count": len(items)}
```

Missing or invalid path/query/header values yield 422. Invalid JSON bodies yield 400; schema mismatches yield 422; oversized bodies yield 413.
