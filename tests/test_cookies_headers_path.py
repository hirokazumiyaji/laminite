"""Cookie params, multi-value headers, and {name:path} (#26)."""

from __future__ import annotations

import asyncio
from typing import Annotated

import pytest

from laminite import Cookie, Laminite, Response
from laminite.request import Headers, Request
from laminite.router import Router
from laminite.testing import TestClient


def test_response_multiple_set_cookie_headers() -> None:
    resp = Response("ok")
    resp.set_cookie("a", "1")
    resp.set_cookie("b", "2", httponly=True)

    app = Laminite()

    @app.get("/")
    def root() -> Response:
        return resp

    client = TestClient(app)
    result = client.get("/")
    cookies = result.headers.getlist("set-cookie")
    assert len(cookies) == 2
    assert any(c.startswith("a=1") for c in cookies)
    assert any(c.startswith("b=2") and "HttpOnly" in c for c in cookies)
    client.close()


def test_response_headers_accept_sequence_of_pairs() -> None:
    resp = Response(
        "ok",
        headers=[("X-A", "1"), ("X-A", "2"), ("X-B", "3")],
    )
    values = [v.decode("latin-1") for k, v in resp.raw_headers if k == b"x-a"]
    assert values == ["1", "2"]


def test_response_delete_cookie() -> None:
    resp = Response("ok")
    resp.delete_cookie("session")
    raw = next(v.decode("latin-1") for k, v in resp.raw_headers if k == b"set-cookie")
    assert raw.startswith("session=")
    assert "Max-Age=0" in raw


def test_headers_getlist() -> None:
    headers = Headers([(b"set-cookie", b"a=1"), (b"set-cookie", b"b=2"), (b"x-id", b"1")])
    assert headers["set-cookie"] == "b=2"
    assert headers.getlist("set-cookie") == ["a=1", "b=2"]
    assert headers.getlist("missing") == []


def test_cookie_param_binding_and_validation() -> None:
    app = Laminite()

    @app.get("/me")
    def me(session_id: Annotated[str, Cookie(alias="session_id")]) -> dict[str, str]:
        return {"session_id": session_id}

    client = TestClient(app)
    ok = client.get("/me", headers={"cookie": "session_id=abc; other=x"})
    assert ok.status_code == 200
    assert ok.json() == {"session_id": "abc"}

    missing = client.get("/me")
    assert missing.status_code == 422

    typed = Laminite()

    @typed.get("/n")
    def n(count: Annotated[int, Cookie()]) -> dict[str, int]:
        return {"count": count}

    client2 = TestClient(typed)
    assert client2.get("/n", headers={"cookie": "count=3"}).json() == {"count": 3}
    assert client2.get("/n", headers={"cookie": "count=nope"}).status_code == 422
    client.close()
    client2.close()


def test_request_cookies_lazy_parse() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"cookie", b"a=1; b=two")],
        "query_string": b"",
        "state": {},
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.disconnect"}

    req = Request(scope, receive)
    assert req._cookies is None
    assert req.cookies == {"a": "1", "b": "two"}
    assert req._cookies is not None


def test_multiple_cookie_headers_are_merged() -> None:
    app = Laminite()

    @app.get("/me")
    def me(
        a: Annotated[str, Cookie()],
        b: Annotated[str, Cookie()],
    ) -> dict[str, str]:
        return {"a": a, "b": b}

    client = TestClient(app)
    assert client.get("/me", headers={"cookie": "a=1; b=2"}).json() == {"a": "1", "b": "2"}
    client.close()

    # ASGI scopes can carry duplicate Cookie header fields.
    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    async def run() -> None:
        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/me",
                "raw_path": b"/me",
                "query_string": b"",
                "headers": [(b"cookie", b"a=1"), (b"cookie", b"b=2")],
                "client": ("test", 50000),
                "server": ("test", 80),
            },
            receive,
            send,
        )

    asyncio.run(run())
    body = b""
    for message in messages:
        if message["type"] == "http.response.body":
            body += message.get("body", b"")  # type: ignore[operator]
    assert body == b'{"a":"1","b":"2"}'


def test_routes_without_cookie_args_do_not_require_cookie_header() -> None:
    app = Laminite()

    @app.get("/")
    def root() -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app)
    assert client.get("/").json() == {"ok": "1"}
    client.close()


def test_path_wildcard_binds_remaining_segments() -> None:
    router: Router[str] = Router()
    router.add("/files/{file_path:path}", "GET", "files")
    match = router.resolve("GET", "/files/a/b/c.txt")
    assert match.value == "files"
    assert match.path_params == {"file_path": "a/b/c.txt"}


def test_path_wildcard_not_at_end_raises() -> None:
    router: Router[str] = Router()
    with pytest.raises(ValueError, match=r"final segment"):
        router.add("/files/{file_path:path}/meta", "GET", "bad")


def test_path_wildcard_via_app() -> None:
    app = Laminite()

    @app.get("/files/{file_path:path}")
    def read_file(file_path: str) -> dict[str, str]:
        return {"file_path": file_path}

    client = TestClient(app)
    assert client.get("/files/a/b/c.txt").json() == {"file_path": "a/b/c.txt"}
    assert client.get("/missing/a").status_code == 404
    client.close()


def test_path_wildcard_empty_remainder_and_head() -> None:
    router: Router[str] = Router()
    router.add("/files/{file_path:path}", "GET", "files")
    match = router.resolve("GET", "/files")
    assert match.path_params == {"file_path": ""}
    head = router.resolve("HEAD", "/files/x/y")
    assert head.is_head is True
    assert head.path_params == {"file_path": "x/y"}


def test_path_wildcard_method_not_allowed() -> None:
    from laminite.router import MethodNotAllowed

    router: Router[str] = Router()
    router.add("/files/{file_path:path}", "GET", "files")
    with pytest.raises(MethodNotAllowed) as exc_info:
        router.resolve("POST", "/files/a/b")
    assert "GET" in exc_info.value.allowed


def test_conflicting_path_param_names() -> None:
    router: Router[str] = Router()
    router.add("/files/{file_path:path}", "GET", "a")
    with pytest.raises(ValueError, match=r"Conflicting path parameter"):
        router.add("/files/{other:path}", "POST", "b")


def test_path_wildcard_matches_when_specific_route_method_differs() -> None:
    """Like Starlette: wrong method on a leaf continues to {name:path}."""
    router: Router[str] = Router()
    router.add("/fixed/{rest:path}", "GET", "wild")
    router.add("/fixed/item", "POST", "post")

    assert router.resolve("POST", "/fixed/item").value == "post"
    match = router.resolve("GET", "/fixed/item")
    assert match.value == "wild"
    assert match.path_params == {"rest": "item"}


def test_set_cookie_options() -> None:
    from datetime import UTC, datetime

    resp = Response("ok")
    resp.set_cookie(
        "tok",
        "v",
        max_age=60,
        expires=datetime(2020, 1, 1, tzinfo=UTC),
        domain="example.com",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    raw = next(v.decode("latin-1") for k, v in resp.raw_headers if k == b"set-cookie")
    assert "Domain=example.com" in raw
    assert "Secure" in raw
    assert "HttpOnly" in raw
    assert "SameSite=strict" in raw


def test_mapping_headers_on_response() -> None:
    resp = Response("ok", headers={"X-One": "1"})
    assert (b"x-one", b"1") in resp.raw_headers


def test_test_headers_mapping_api() -> None:
    from laminite.testing import TestHeaders

    headers = TestHeaders([("X-A", "1"), ("X-A", "2")])
    assert headers["x-a"] == "2"
    assert headers.get("x-a") == "2"
    assert headers.getlist("x-a") == ["1", "2"]
    assert len(headers) == 1
    assert list(headers) == ["x-a"]
