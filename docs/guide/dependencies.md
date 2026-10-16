# Dependency injection

Dependencies are keyed by type and resolved at freeze time.

- `app.provide(T, instance)` — app scope (often registered inside lifespan)
- `@app.provider` — request scope (function, async, generator, or async generator)

```python
from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from laminite import Laminite, Request


@dataclass
class Pool:
    name: str


@dataclass
class Conn:
    pool: Pool


@dataclass
class User:
    name: str


@asynccontextmanager
async def lifespan(app: Laminite):
    app.provide(Pool, Pool("main"))
    yield


app = Laminite(lifespan=lifespan)


@app.provider
def connection(pool: Pool) -> Iterator[Conn]:
    yield Conn(pool)


@app.provider
def current_user(request: Request) -> User:
    return User(request.headers.get("authorization") or "anon")


@app.get("/me")
def me(user: User, conn: Conn):
    return {"user": user.name, "pool": conn.pool.name}
```

In tests, override dependencies with `TestClient(app, overrides={Pool: FakePool()})`.
