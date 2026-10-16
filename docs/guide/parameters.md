# Parameters

Argument sources are decided at freeze time, in this order:

1. Path template name → path
2. Type is `Request` → request
3. `Annotated` metadata `Header` / `Cookie` / `Query` / `Body` → that source
4. Type registered as a dependency → dependency
5. `msgspec.Struct` subclass → body
6. Otherwise → query (must be a supported scalar / collection type)

Cookie arguments are validated with the same TypedDict + `msgspec.convert` path as query/header. Routes without Cookie parameters never parse the `Cookie` header.

```python
from typing import Annotated

from laminite import Body, Cookie, Header, Laminite, Query
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
    session_id: Annotated[str | None, Cookie(alias="session_id")] = None,
):
    return {
        "item_id": item_id,
        "q": q,
        "page_size": page_size,
        "request_id": request_id,
        "session_id": session_id,
    }


@app.post("/bulk")
async def bulk(items: Annotated[list[Item], Body()]):
    return {"count": len(items)}
```

Missing or invalid path/query/header/cookie values yield 422. Invalid JSON bodies yield 400; schema mismatches yield 422; oversized bodies yield 413. JSON body routes require `Content-Type: application/json` (or `application/*+json`); otherwise 415. Too many query fields yield 400 (`Laminite(max_query_fields=...)`).

## Supported path / query / header types

Values are converted with `msgspec.convert(..., strict=False)`. The following types are supported:

| Type | Notes |
|------|--------|
| `str`, `int`, `float`, `bool`, `bytes` | Basics |
| `list[T]` | Query only (repeated keys); not allowed on path |
| `T \| None` / `Optional[T]` | Optional parameters |
| `uuid.UUID` | Parsed from canonical string form |
| `enum.Enum` subclasses (`StrEnum`, `IntEnum`, …) | Member names / values per msgspec |
| `typing.Literal[...]` | Closed set of values |
| `datetime.datetime`, `date`, `time` | ISO-8601 strings |
| `decimal.Decimal` | Decimal strings |
| `typing.NewType` | When the underlying type is supported |

Constraints via `msgspec.Meta` are preserved on path / query / header (and work together with `Query` / `Header` markers):

```python
from typing import Annotated

import msgspec
from laminite import Laminite, Query

app = Laminite()


@app.get("/page")
async def page(
    n: Annotated[int, Query(alias="p"), msgspec.Meta(ge=1, le=100)] = 1,
):
    return {"n": n}
```

User-defined classes that are not registered dependencies (including when wrapped in `Optional` or `list`) raise `ValueError` at freeze time.
