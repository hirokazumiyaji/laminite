# Routing

Path templates start with `/`. Parameters use `{name}` and must occupy a whole segment. `{name}` must be a valid Python identifier. Partial segments such as `/files/{name}.json` are rejected.

Trailing slashes are significant: `/items` and `/items/` are different paths. Laminite does not redirect.

When a path matches but the method does not, Laminite returns 405 with an `Allow` header. If `GET` is registered, `HEAD` is included in `Allow`.

`HEAD` requests fall back to the `GET` handler. The response keeps the real `content-length` and sends an empty body.
