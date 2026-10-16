from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from laminite import Laminite
from laminite.testing import TestClient


def test_startup_failure() -> None:
    @asynccontextmanager
    async def lifespan(app: Laminite) -> AsyncIterator[None]:
        raise RuntimeError("startup boom")
        yield  # pragma: no cover

    app = Laminite(lifespan=lifespan)
    with pytest.raises(RuntimeError, match="startup"), TestClient(app):
        pass


def test_handler_error_at_startup() -> None:
    app = Laminite()

    @app.get("/bad")
    def bad(**kwargs: object) -> None:
        return None

    with pytest.raises(RuntimeError, match="kwargs"), TestClient(app):
        pass


def test_lifespan_value_on_same_loop() -> None:
    @asynccontextmanager
    async def lifespan(app: Laminite) -> AsyncIterator[None]:
        app.provide(str, "hello")
        yield

    app = Laminite(lifespan=lifespan)

    @app.get("/")
    def root(msg: str) -> dict[str, str]:
        return {"msg": msg}

    with TestClient(app) as client:
        assert client.get("/").json() == {"msg": "hello"}
