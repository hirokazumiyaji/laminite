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

## Observability hooks

Matched routes publish their path template on the ASGI scope as `laminite.route` (also `Request.route`). Middleware that wraps the app can read it after the inner call returns. Router-level 404s and 405s leave the key unset; a handler that raises `HTTPException(404)` keeps its matched template.

```python
@app.get("/items/{item_id}")
async def read_item(item_id: int, request: Request):
    assert request.route == "/items/{item_id}"
```

Pass `on_unhandled_exception=` to observe unexpected errors (no matching exception handler, or a handler that itself fails). An uncaught `HTTPException` from a route handler is treated as a normal error response and does not invoke the hook, nor do successfully handled exceptions. An exception handler that itself raises (including raising `HTTPException`) does invoke the hook, and the hook receives the handler's exception, not the original one. Sync and async callables are both accepted; hook failures are logged and the client still receives 500.

```python
def on_exc(request: Request, exc: BaseException) -> None:
    # e.g. span.record_exception(exc)
    ...


app = Laminite(on_unhandled_exception=on_exc)
app.add_middleware(OpenTelemetryMiddleware)  # pure ASGI middleware
```

For a batteries-included setup, install the workspace package `laminite-opentelemetry` and call `instrument_app(app)` before the first request. It wraps the official ASGI middleware, sets `http.route` from `laminite.route`, and records unhandled exceptions on the active span.
