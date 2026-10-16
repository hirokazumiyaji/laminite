# Responses

| Return value | Content-Type | Body |
|---|---|---|
| `Response` / `JSONResponse` | as set on the object | as set on the object |
| `None` | (none) | empty |
| `bytes` | `application/octet-stream` | raw bytes |
| `str` | `text/plain; charset=utf-8` | UTF-8 |
| anything else | `application/json` | msgspec JSON |

If `status_code=` is omitted on the route, `None` becomes 204 and other values become 200. When the handler returns a `Response`, the route `status_code` is ignored.
