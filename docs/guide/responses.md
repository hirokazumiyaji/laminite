# Responses

| Return value | Content-Type | Body |
|---|---|---|
| `Response` / `JSONResponse` | as set on the object | as set on the object |
| `StreamingResponse` | as set on the object | chunked (no Content-Length) |
| `None` | (none) | empty |
| `bytes` | `application/octet-stream` | raw bytes |
| `str` | `text/plain; charset=utf-8` | UTF-8 |
| anything else | `application/json` | msgspec JSON |

If `status_code=` is omitted on the route, `None` becomes 204 and other values become 200. When the handler returns a `Response` or `StreamingResponse`, the route `status_code` is ignored.

## Streaming

`StreamingResponse` accepts a sync or async iterator of `bytes` or `str`. Chunks are sent with `more_body=True`; request-scoped providers stay open until the stream finishes.

```python
from laminite import Laminite, StreamingResponse

app = Laminite()


@app.get("/events")
async def events():
    async def gen():
        yield "data: hello\n\n"
        yield "data: world\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
```
