"""Cross-feature fixes for cookies, OpenAPI, APIRouter, and streaming (#44)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

import msgspec
import pytest
from msgspec import Struct

from laminite import APIRouter, Cookie, Laminite, Response, StreamingResponse
from laminite.testing import TestClient


class Item(Struct):
    name: str


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"path": "/\r\nX-Injected: 1"}, "cookie path"),
        ({"domain": "example.com\r\nX-Injected: 1"}, "cookie domain"),
        ({"expires": "Wed, 21 Oct 2015 07:28:00 GMT\r\nX-Injected: 1"}, "cookie expires"),
    ],
)
def test_set_cookie_rejects_crlf_in_attributes(kwargs: dict[str, str], match: str) -> None:
    resp = Response("ok")
    with pytest.raises(ValueError, match=match):
        resp.set_cookie("sid", "x", **kwargs)
    assert not any(key == b"set-cookie" for key, _ in resp.raw_headers)


def test_set_cookie_rejects_crlf_in_name_and_value() -> None:
    for name in ("sid\r\nX-Injected: 1", "bad name", "bad;name"):
        with pytest.raises(ValueError):
            Response("ok").set_cookie(name, "x")

    for value in ("x\r\nX-Injected: 1", "x\ny", "x\x00y"):
        with pytest.raises(ValueError):
            Response("ok").set_cookie("sid", value)


def test_streaming_response_set_cookie_validates_and_appends_headers() -> None:
    response = StreamingResponse(
        [b"x"],
        headers=[("x-multi", "1"), ("x-multi", "2")],
    )
    response.set_cookie("sid", "abc", httponly=True)
    response.delete_cookie("old")
    values = [v.decode("latin-1") for k, v in response.raw_headers if k == b"x-multi"]
    assert values == ["1", "2"]
    cookies = [v.decode("latin-1") for k, v in response.raw_headers if k == b"set-cookie"]
    assert any(c.startswith("sid=abc") and "HttpOnly" in c for c in cookies)
    assert any(c.startswith("old=") and "Max-Age=0" in c for c in cookies)

    with pytest.raises(ValueError, match="cookie path"):
        response.set_cookie("sid", "abc", path="/\r\nX-Injected: 1")


def test_cookie_param_openapi_output_includes_msgspec_meta() -> None:
    app = Laminite()

    @app.get("/me")
    def me(
        cid: Annotated[int, Cookie(alias="cid"), msgspec.Meta(ge=1)],
    ) -> dict[str, int]:
        return {"cid": cid}

    doc = app.openapi()
    parameters = doc["paths"]["/me"]["get"]["parameters"]
    assert parameters == [
        {
            "name": "cid",
            "in": "cookie",
            "required": True,
            "schema": {"type": "integer", "minimum": 1},
        }
    ]

    client = TestClient(app)
    assert client.get("/me", headers={"cookie": "cid=3"}).json() == {"cid": 3}
    assert client.get("/me", headers={"cookie": "cid=0"}).status_code == 422
    client.close()


def test_api_router_route_max_body_size_is_applied_after_include() -> None:
    router = APIRouter()

    @router.post("/upload", max_body_size=5)
    def upload(item: Item) -> dict[str, str]:
        return {"name": item.name}

    app = Laminite(max_body_size=1_000_000)
    app.include_router(router)

    client = TestClient(app)
    res = client.post("/upload", json={"name": "too-large"})
    assert res.status_code == 413
    client.close()


def test_api_router_all_methods_accept_max_body_size() -> None:
    router = APIRouter()

    for method in ("get", "post", "put", "patch", "delete"):
        decorator = getattr(router, method)

        @decorator("/x", max_body_size=1)
        def handler() -> dict[str, str]:
            return {"ok": "1"}

    infos = list(router.iter_route_infos())
    assert [info.max_body_size for info in infos] == [1, 1, 1, 1, 1]


def test_openapi_streaming_response_is_not_treated_as_json() -> None:
    app = Laminite()

    @app.get("/stream")
    def stream() -> StreamingResponse:
        def gen() -> Iterator[bytes]:
            yield b"x"

        return StreamingResponse(gen(), media_type="text/event-stream")

    response = app.openapi()["paths"]["/stream"]["get"]["responses"]["default"]
    assert response == {
        "description": "Response",
        "content": {"*/*": {"schema": {}}},
    }
