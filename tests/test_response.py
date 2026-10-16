import asyncio

from laminite.response import JSONResponse, Response, render_result, send_response


def test_render_table() -> None:
    status, headers, body = render_result(None, status_code=204)
    assert status == 204
    assert body == b""
    assert headers == []

    status, headers, body = render_result(b"bin", status_code=200)
    assert body == b"bin"
    assert headers == [(b"content-type", b"application/octet-stream")]

    status, headers, body = render_result("hi", status_code=200)
    assert body == b"hi"
    assert headers == [(b"content-type", b"text/plain; charset=utf-8")]

    status, headers, body = render_result({"a": 1}, status_code=200)
    assert body == b'{"a":1}'
    assert headers == [(b"content-type", b"application/json")]

    resp = Response(b"x", status_code=201, media_type="application/custom")
    status, headers, body = render_result(resp, status_code=200)
    assert status == 201
    assert body == b"x"

    j = JSONResponse({"ok": True}, status_code=201)
    status, headers, body = render_result(j, status_code=200)
    assert status == 201
    assert body == b'{"ok":true}'


def test_head_content_length() -> None:
    async def _head_test() -> None:
        messages: list[dict] = []

        async def send(message: dict) -> None:
            messages.append(message)

        await send_response(
            send,
            status_code=200,
            headers=[(b"content-type", b"text/plain; charset=utf-8")],
            body=b"hello",
            is_head=True,
        )
        assert messages[0]["headers"][-1] == (b"content-length", b"5")
        assert messages[1]["body"] == b""

    asyncio.run(_head_test())
