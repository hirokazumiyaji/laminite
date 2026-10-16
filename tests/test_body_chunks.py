import asyncio

from msgspec import Struct

from laminite import Laminite


class Item(Struct):
    name: str


def test_chunked_and_oversized_body() -> None:
    app = Laminite(max_body_size=20)

    @app.post("/items")
    async def create(item: Item) -> dict[str, str]:
        return {"name": item.name}

    async def call(body_chunks: list[bytes]) -> tuple[int, bytes]:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/items",
            "raw_path": b"/items",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("test", 1),
            "server": ("test", 80),
            "state": {},
        }
        idx = 0

        async def receive() -> dict:
            nonlocal idx
            if idx < len(body_chunks):
                chunk = body_chunks[idx]
                idx += 1
                return {
                    "type": "http.request",
                    "body": chunk,
                    "more_body": idx < len(body_chunks),
                }
            return {"type": "http.disconnect"}

        messages: list[dict] = []

        async def send(message: dict) -> None:
            messages.append(message)

        await app(scope, receive, send)
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        return status, body

    status, body = asyncio.run(call([b'{"name":', b'"a"}']))
    assert status == 200
    assert b'"a"' in body

    status, _ = asyncio.run(call([b"012345678901234567890"]))
    assert status == 413
