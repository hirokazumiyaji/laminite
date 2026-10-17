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
