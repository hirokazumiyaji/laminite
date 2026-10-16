import asyncio

from laminite.request import Request


def test_lazy_headers_and_query() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-test", b"1")],
        "query_string": b"q=1",
        "state": {},
    }

    async def receive() -> dict:
        return {"type": "http.disconnect"}

    req = Request(scope, receive)
    # Touching method/path must not parse headers/query
    assert req.method == "GET"
    assert req.path == "/"
    assert req._headers is None
    assert req._query_params is None
    assert req.headers["x-test"] == "1"
    assert req.query_params["q"] == "1"


def test_body_cached() -> None:
    calls = 0

    async def receive() -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": b'{"a":1}', "more_body": False}
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "state": {},
    }
    req = Request(scope, receive)

    async def run() -> None:
        assert await req.body() == b'{"a":1}'
        assert await req.body() == b'{"a":1}'
        assert calls == 1

    asyncio.run(run())
