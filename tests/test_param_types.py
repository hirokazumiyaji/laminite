"""Parameter type resolution, msgspec.Meta, and standard scalar types (#20)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, NewType
from uuid import UUID

import msgspec
import pytest

from laminite import Body, Header, Laminite, Query
from laminite.testing import TestClient

UserId = NewType("UserId", int)


@dataclass
class User:
    name: str


class Color(StrEnum):
    RED = "red"
    BLUE = "blue"


class Item(msgspec.Struct):
    name: str


def test_unregistered_optional_dependency_rejected_at_freeze() -> None:
    app = Laminite()

    @app.get("/")
    def root(user: User | None = None) -> None:
        return None

    client = TestClient(app)
    with pytest.raises(ValueError, match="Unregistered"):
        client.get("/")
    client.close()


def test_unregistered_list_dependency_rejected_at_freeze() -> None:
    app = Laminite()

    @app.get("/")
    def root(users: list[User]) -> None:
        return None

    client = TestClient(app)
    with pytest.raises(ValueError, match="Unregistered"):
        client.get("/")
    client.close()


def test_msgspec_meta_on_query_param_enforced() -> None:
    app = Laminite()

    @app.get("/page")
    def page(n: Annotated[int, msgspec.Meta(ge=1)] = 1) -> dict[str, int]:
        return {"n": n}

    client = TestClient(app)
    assert client.get("/page", query={"n": "5"}).json() == {"n": 5}
    assert client.get("/page", query={"n": "0"}).status_code == 422
    client.close()


def test_query_marker_and_msgspec_meta_both_apply() -> None:
    app = Laminite()

    @app.get("/page")
    def page(
        n: Annotated[int, Query(alias="p"), msgspec.Meta(ge=1)] = 1,
    ) -> dict[str, int]:
        return {"n": n}

    client = TestClient(app)
    assert client.get("/page", query={"p": "3"}).json() == {"n": 3}
    assert client.get("/page", query={"p": "0"}).status_code == 422
    client.close()


def test_annotated_list_path_param_rejected_at_freeze() -> None:
    app = Laminite()

    @app.get("/items/{item_ids}")
    def read(item_ids: Annotated[list[int], "meta"]) -> None:
        return None

    client = TestClient(app)
    with pytest.raises(TypeError, match="list"):
        client.get("/items/1")
    client.close()


def test_optional_annotated_meta_query_param_enforced() -> None:
    app = Laminite()

    @app.get("/page")
    def page(
        n: Annotated[int, msgspec.Meta(ge=1)] | None = None,
    ) -> dict[str, int | None]:
        return {"n": n}

    client = TestClient(app)
    assert client.get("/page", query={"n": "5"}).json() == {"n": 5}
    assert client.get("/page", query={"n": "0"}).status_code == 422
    assert client.get("/page").json() == {"n": None}
    client.close()


def test_list_of_annotated_meta_query_param_enforced() -> None:
    app = Laminite()

    @app.get("/items")
    def items(
        ids: list[Annotated[int, msgspec.Meta(ge=1)]] | None = None,
    ) -> dict[str, list[int] | None]:
        return {"ids": ids}

    client = TestClient(app)
    assert client.get("/items", query={"ids": ["5", "10"]}).json() == {"ids": [5, 10]}
    assert client.get("/items", query={"ids": ["5", "0"]}).status_code == 422
    client.close()


def test_optional_struct_auto_detected_as_body() -> None:
    app = Laminite()

    @app.post("/items")
    def create(body: Item | None = None) -> dict[str, str | None]:
        return {"name": body.name if body else None}

    client = TestClient(app)
    assert client.post("/items", json={"name": "x"}).json() == {"name": "x"}
    assert client.post("/items").json() == {"name": None}
    client.close()


def test_uuid_strenum_literal_date_decimal_path_query_header() -> None:
    app = Laminite()

    @app.get("/things/{thing_id}")
    def read(
        thing_id: UUID,
        color: Color,
        kind: Literal["a", "b"],
        day: date,
        amount: Decimal,
        created: datetime | None = None,
        at: time | None = None,
        user_id: UserId | None = None,
        x_trace: Annotated[UUID | None, Header(alias="x-trace")] = None,
    ) -> dict[str, object]:
        return {
            "thing_id": str(thing_id),
            "color": color,
            "kind": kind,
            "day": day.isoformat(),
            "amount": str(amount),
            "created": created.isoformat() if created else None,
            "at": at.isoformat() if at else None,
            "user_id": int(user_id) if user_id is not None else None,
            "x_trace": str(x_trace) if x_trace is not None else None,
        }

    uid = "550e8400-e29b-41d4-a716-446655440000"
    client = TestClient(app)
    res = client.get(
        f"/things/{uid}",
        query={
            "color": "red",
            "kind": "a",
            "day": "2024-01-15",
            "amount": "12.50",
            "created": "2024-01-15T12:00:00",
            "at": "12:30:00",
            "user_id": "7",
        },
        headers={"x-trace": uid},
    )
    assert res.status_code == 200
    assert res.json() == {
        "thing_id": uid,
        "color": "red",
        "kind": "a",
        "day": "2024-01-15",
        "amount": "12.50",
        "created": "2024-01-15T12:00:00",
        "at": "12:30:00",
        "user_id": 7,
        "x_trace": uid,
    }

    assert (
        client.get(
            f"/things/{uid}",
            query={"color": "green", "kind": "a", "day": "2024-01-15", "amount": "1"},
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"/things/{uid}",
            query={"color": "red", "kind": "c", "day": "2024-01-15", "amount": "1"},
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"/things/{uid}",
            query={"color": "red", "kind": "a", "day": "not-a-date", "amount": "1"},
        ).status_code
        == 422
    )
    assert client.get("/things/not-a-uuid").status_code == 422
    client.close()


def test_annotated_optional_list_query_param_collects_repeated_keys() -> None:
    app = Laminite()

    @app.get("/items")
    def items(
        ids: Annotated[list[int], msgspec.Meta(min_length=1)] | None = None,
    ) -> dict[str, list[int] | None]:
        return {"ids": ids}

    client = TestClient(app)
    assert client.get("/items", query={"ids": ["5", "10"]}).json() == {"ids": [5, 10]}
    client.close()


def test_annotated_optional_list_query_param_meta_enforced() -> None:
    app = Laminite()

    @app.get("/items")
    def items(
        ids: Annotated[list[int], msgspec.Meta(max_length=1)] | None = None,
    ) -> dict[str, list[int] | None]:
        return {"ids": ids}

    client = TestClient(app)
    assert client.get("/items", query={"ids": ["5"]}).json() == {"ids": [5]}
    assert client.get("/items", query={"ids": ["5", "10"]}).status_code == 422
    client.close()


def test_annotated_optional_dependency_resolved() -> None:
    app = Laminite()
    app.provide(User, User("real"))

    @app.get("/user")
    def read(user: Annotated[User, msgspec.Meta()] | None = None) -> dict[str, str]:
        assert user is not None
        return {"name": user.name}

    client = TestClient(app)
    assert client.get("/user").json() == {"name": "real"}
    client.close()


def test_annotated_optional_struct_body_detected() -> None:
    app = Laminite()

    @app.post("/items")
    def create(
        body: Annotated[Item, msgspec.Meta()] | None = None,
    ) -> dict[str, str | None]:
        return {"name": body.name if body else None}

    client = TestClient(app)
    assert client.post("/items", json={"name": "x"}).json() == {"name": "x"}
    assert client.post("/items").json() == {"name": None}
    client.close()


def test_annotated_optional_list_body_marker_detected() -> None:
    app = Laminite()

    @app.post("/items")
    def create(
        body: Annotated[list[Item], Body()] | None = None,
    ) -> dict[str, int]:
        return {"count": len(body or [])}

    client = TestClient(app)
    assert client.post("/items", json=[{"name": "x"}, {"name": "y"}]).json() == {"count": 2}
    client.close()
