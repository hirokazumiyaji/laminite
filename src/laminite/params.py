"""Argument markers for Annotated metadata."""

from __future__ import annotations


class Query:
    def __init__(self, *, alias: str | None = None) -> None:
        self.alias = alias


class Header:
    def __init__(self, *, alias: str | None = None) -> None:
        self.alias = alias


class Body:
    def __init__(self) -> None:
        pass
