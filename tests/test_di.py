from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest

from laminite import HTTPException, Laminite, Request
from laminite.testing import TestClient


@dataclass
class Pool:
    name: str


@dataclass
class Conn:
    pool: Pool
    closed: bool = False


@dataclass
class User:
    name: str


def test_di_example_with_fakes() -> None:
    @asynccontextmanager
    async def lifespan(app: Laminite) -> AsyncIterator[None]:
        app.provide(Pool, Pool("main"))
        yield

    app = Laminite(lifespan=lifespan)

    @app.provider
    def connection(pool: Pool) -> Iterator[Conn]:
        conn = Conn(pool)
        yield conn
        conn.closed = True

    @app.provider
    def current_user(request: Request) -> User:
        auth = request.headers.get("authorization") or "anon"
        return User(auth)

    calls = {"conn": 0}

    @app.get("/me")
    def me(user: User, conn: Conn) -> dict[str, str]:
        calls["conn"] += 1
        assert conn.pool.name == "main"
        return {"user": user.name, "pool": conn.pool.name}

    with TestClient(app) as client:
        res = client.get("/me", headers={"authorization": "alice"})
        assert res.json() == {"user": "alice", "pool": "main"}
        client.get("/me", headers={"authorization": "bob"})
    assert calls["conn"] == 2


def test_provider_called_once_per_request() -> None:
    app = Laminite()
    counts = {"n": 0}

    @dataclass
    class Token:
        value: int

    @app.provider
    def token() -> Token:
        counts["n"] += 1
        return Token(counts["n"])

    @app.get("/")
    def root(a: Token, b: Token) -> dict[str, int]:
        assert a is b
        return {"v": a.value}

    client = TestClient(app)
    assert client.get("/").json() == {"v": 1}
    assert counts["n"] == 1
    client.close()


def test_generator_sees_handler_exception() -> None:
    app = Laminite()
    seen: list[str] = []

    @dataclass
    class Tx:
        pass

    @app.provider
    def tx() -> Iterator[Tx]:
        try:
            yield Tx()
            seen.append("commit")
        except Exception:
            seen.append("rollback")
            raise

    @app.get("/boom")
    def boom(t: Tx) -> None:
        raise RuntimeError("fail")

    client = TestClient(app)
    assert client.get("/boom").status_code == 500
    assert seen == ["rollback"]
    client.close()


def test_cleanup_exception_becomes_error_response() -> None:
    app = Laminite()

    @dataclass
    class Res:
        pass

    @app.provider
    def res() -> Iterator[Res]:
        yield Res()
        raise HTTPException(503, "cleanup failed")

    @app.get("/")
    def root(r: Res) -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 503
    assert response.json() == {"detail": "cleanup failed"}
    client.close()


def test_unregistered_and_cycle_and_duplicate() -> None:
    app = Laminite()

    @dataclass
    class A:
        pass

    @dataclass
    class B:
        pass

    @app.get("/")
    def root(a: A) -> None:
        return None

    client = TestClient(app)
    with pytest.raises(ValueError, match="Unregistered"):
        client.get("/")
    client.close()

    app2 = Laminite()

    @app2.provider
    def a(b: B) -> A:
        return A()

    @app2.provider
    def b(a: A) -> B:
        return B()

    @app2.get("/")
    def root2(a: A) -> dict[str, str]:
        return {"ok": "1"}

    client2 = TestClient(app2)
    with pytest.raises(ValueError, match="Circular"):
        client2.get("/")
    client2.close()

    app3 = Laminite()
    app3.provide(A, A())
    with pytest.raises(ValueError):
        app3.provide(A, A())


def test_overrides() -> None:
    app = Laminite()

    @dataclass
    class Svc:
        name: str

    app.provide(Svc, Svc("real"))

    @app.get("/")
    def root(svc: Svc) -> dict[str, str]:
        return {"name": svc.name}

    with TestClient(app, overrides={Svc: Svc("fake")}) as client:
        assert client.get("/").json() == {"name": "fake"}


def test_provider_annotated_dependency() -> None:
    from typing import Annotated

    app = Laminite()

    @dataclass
    class Svc:
        name: str

    @dataclass
    class Label:
        text: str

    app.provide(Svc, Svc("ok"))

    @app.provider
    def label(svc: Annotated[Svc, "meta"]) -> Label:
        return Label(svc.name)

    @app.get("/")
    def root(label: Label) -> dict[str, str]:
        return {"label": label.text}

    client = TestClient(app)
    assert client.get("/").json() == {"label": "ok"}
    client.close()


def test_provider_with_request_and_dependency() -> None:
    app = Laminite()

    @dataclass
    class Cfg:
        prefix: str

    app.provide(Cfg, Cfg("hi"))

    @dataclass
    class Greeting:
        text: str

    @app.provider
    def greeting(cfg: Cfg, request: Request) -> Greeting:
        name = request.headers.get("x-name") or "world"
        return Greeting(f"{cfg.prefix} {name}")

    @app.get("/")
    def root(greeting: Greeting) -> dict[str, str]:
        return {"text": greeting.text}

    client = TestClient(app)
    assert client.get("/", headers={"x-name": "laminite"}).json() == {"text": "hi laminite"}
    client.close()


def test_override_replaces_provider_with_deps() -> None:
    app = Laminite()
    calls = {"n": 0}

    @dataclass
    class Dep:
        pass

    @dataclass
    class Svc:
        name: str

    @app.provider
    def dep() -> Dep:
        calls["n"] += 1
        return Dep()

    @app.provider
    def svc(dep: Dep) -> Svc:
        calls["n"] += 1
        return Svc("real")

    @app.get("/")
    def root(svc: Svc) -> dict[str, str]:
        return {"name": svc.name}

    with TestClient(app, overrides={Svc: Svc("fake")}) as client:
        assert client.get("/").json() == {"name": "fake"}
    assert calls["n"] == 0


def test_no_di_in_pipeline_when_unused() -> None:
    from laminite.compiler import RouteInfo, analyze_handler, path_param_names
    from laminite.di import DependencyRegistry

    def plain() -> dict[str, str]:
        return {"ok": "1"}

    info = RouteInfo(
        path="/",
        methods=("GET",),
        handler=plain,
        status_code=None,
        offload=False,
        path_param_names=path_param_names("/"),
    )
    compiled = analyze_handler(info, DependencyRegistry())
    assert compiled.request_providers == []
    assert compiled.dep_arg_names == {}
