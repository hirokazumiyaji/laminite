# Migrating from FastAPI

| FastAPI | Laminite |
|---|---|
| `Depends()` | type-keyed `provide` / `provider` |
| Pydantic models | `msgspec.Struct` |
| OpenAPI generation | not in v0.1 |
| `APIRouter` mounts | `APIRouter` + `include_router` (flattened at freeze) |
| Runtime dependency resolution | freeze-time resolution only |
| `TestClient` (httpx) | built-in `laminite.testing.TestClient` |

Query/header aliases use `Annotated[T, Query(alias=...)]` / `Header(alias=...)`.
