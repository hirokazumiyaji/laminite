# Lifespan, middleware, and exceptions

## Lifespan

```python
from contextlib import asynccontextmanager

from laminite import Laminite


@asynccontextmanager
async def lifespan(app: Laminite):
    # startup
    yield
    # shutdown


app = Laminite(lifespan=lifespan)
```

Freeze runs after lifespan startup completes (or on the first HTTP request if the server never sends lifespan messages). Lifespan is not wrapped by middleware.

## Middleware

Pure ASGI middleware:

```python
app.add_middleware(Timing, header="x-response-time")
```

Earlier registrations wrap later ones (outermost first).

## Exception handlers

```python
from laminite import JSONResponse, Request


@app.exception_handler(UserNotFound)
def on_missing(request: Request, exc: UserNotFound) -> JSONResponse:
    return JSONResponse({"detail": "user not found"}, status_code=404)
```

Handlers match by MRO. They cover exceptions from handlers and providers, including `HTTPException` raised while reading the request body (for example 413 when `max_body_size` is exceeded).

## OpenAPI

Pass `openapi_url="/openapi.json"` to serve a freeze-time OpenAPI 3.1 document. Generation uses `msgspec.json.schema_components` (no extra dependencies). The JSON is encoded once at freeze; request handling only returns the cached bytes.

```python
app = Laminite(title="Demo API", version="1.0.0", openapi_url="/openapi.json")
```

`app.openapi()` returns the same document as a dict. Omit `openapi_url` (or set it to `None`) to skip registering the route.
