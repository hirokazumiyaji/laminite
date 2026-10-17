"""Hot-path allocation reductions (#23)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from unittest.mock import patch

from laminite import Laminite
from laminite.router import Router
from laminite.testing import TestClient


@dataclass
class Svc:
    name: str


def test_static_resolve_reuses_prebuilt_match() -> None:
    router: Router[str] = Router()
    router.add("/hello", "GET", "hello")
    first = router.resolve("GET", "/hello")
    second = router.resolve("GET", "/hello")
    assert first is second
    assert first.path_params is second.path_params

    head1 = router.resolve("HEAD", "/hello")
    head2 = router.resolve("HEAD", "/hello")
    assert head1 is head2
    assert head1.is_head is True
    assert head1.value == "hello"


def test_async_exit_stack_skipped_for_plain_providers() -> None:
    app = Laminite()

    @app.provider
    def svc() -> Svc:
        return Svc("ok")

    @app.get("/")
    def root(svc: Svc) -> dict[str, str]:
        return {"name": svc.name}

    with patch("laminite.compiler.AsyncExitStack") as stack_cls:
        client = TestClient(app)
        assert client.get("/").json() == {"name": "ok"}
        client.close()
        stack_cls.assert_not_called()


def test_async_exit_stack_used_for_generator_providers() -> None:
    app = Laminite()

    @app.provider
    def svc() -> Iterator[Svc]:
        yield Svc("cm")

    @app.get("/")
    def root(svc: Svc) -> dict[str, str]:
        return {"name": svc.name}

    with patch("laminite.compiler.AsyncExitStack", wraps=AsyncExitStack) as stack_cls:
        client = TestClient(app)
        assert client.get("/").json() == {"name": "cm"}
        client.close()
        assert stack_cls.called


def test_dispatch_does_not_require_preinitialized_state() -> None:
    app = Laminite()

    @app.get("/")
    def root() -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app)
    assert client.get("/").json() == {"ok": "1"}
    client.close()
