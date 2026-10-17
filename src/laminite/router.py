"""Static-map + segment-trie router."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypeVar

V = TypeVar("V")

_PARAM_RE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class NotFound(Exception):
    """No route matched the path."""


@dataclass(slots=True)
class MethodNotAllowed(Exception):
    allowed: frozenset[str]


@dataclass(slots=True)
class Match[V]:
    value: V
    path_params: dict[str, str]
    is_head: bool = False


class _Node[V]:
    __slots__ = ("methods", "param", "param_name", "param_names", "static")

    def __init__(self) -> None:
        self.static: dict[str, _Node[V]] = {}
        self.param: _Node[V] | None = None
        self.param_name: str | None = None
        self.methods: dict[str, V] = {}
        self.param_names: tuple[str, ...] = ()


class Router[V]:
    def __init__(self) -> None:
        self._static: dict[str, dict[str, V]] = {}
        self._root: _Node[V] = _Node()

    def add(self, path: str, method: str, value: V) -> None:
        method = method.upper()
        segments, param_names, is_static = _parse_template(path)

        static_added = False
        if is_static:
            methods = self._static.setdefault(path, {})
            if method in methods:
                raise ValueError(f"Duplicate route: {method} {path}")
            methods[method] = value
            static_added = True

        try:
            node: _Node[V] = self._root
            for segment, is_param, name in segments:
                if is_param:
                    assert name is not None
                    if node.param is None:
                        node.param = _Node[V]()
                        node.param_name = name
                    elif node.param_name != name:
                        raise ValueError(
                            f"Conflicting parameter names at the same position: "
                            f"{node.param_name!r} vs {name!r}"
                        )
                    node = node.param
                else:
                    child = node.static.get(segment)
                    if child is None:
                        child = _Node[V]()
                        node.static[segment] = child
                    node = child

            if method in node.methods:
                raise ValueError(f"Duplicate route: {method} {path}")
            node.methods[method] = value
            node.param_names = param_names
        except Exception:
            if static_added:
                methods = self._static[path]
                del methods[method]
                if not methods:
                    del self._static[path]
            raise

    def resolve(self, method: str, path: str) -> Match[V]:
        method = method.upper()
        static_methods = self._static.get(path)
        if static_methods is not None:
            if method in static_methods:
                return Match(static_methods[method], {})
            if method == "HEAD" and "GET" in static_methods:
                return Match(static_methods["GET"], {}, is_head=True)

        parts = _split_path(path)
        match, allowed = self._search(self._root, parts, 0, [], method)
        if match is not None:
            return match
        if allowed is not None:
            raise MethodNotAllowed(allowed)
        if static_methods is not None:
            raise MethodNotAllowed(_allow_set(static_methods))
        raise NotFound()

    def _search(
        self,
        node: _Node[V],
        parts: list[str],
        index: int,
        values: list[str],
        method: str,
    ) -> tuple[Match[V] | None, frozenset[str] | None]:
        if index == len(parts):
            if not node.methods:
                return None, None
            hit = _match_method(node, values, method)
            if hit is not None:
                return hit, None
            return None, _allow_set(node.methods)

        segment = parts[index]
        allowed: frozenset[str] | None = None

        child = node.static.get(segment)
        if child is not None:
            match, child_allowed = self._search(child, parts, index + 1, values, method)
            if match is not None:
                return match, None
            allowed = _union(allowed, child_allowed)

        if node.param is not None:
            values.append(segment)
            match, child_allowed = self._search(node.param, parts, index + 1, values, method)
            values.pop()
            if match is not None:
                return match, None
            allowed = _union(allowed, child_allowed)

        return None, allowed


def _match_method[V](node: _Node[V], values: list[str], method: str) -> Match[V] | None:
    if method in node.methods:
        params = dict(zip(node.param_names, values, strict=True))
        return Match(node.methods[method], params)
    if method == "HEAD" and "GET" in node.methods:
        params = dict(zip(node.param_names, values, strict=True))
        return Match(node.methods["GET"], params, is_head=True)
    return None


def _allow_set[V](methods: dict[str, V]) -> frozenset[str]:
    allowed = set(methods)
    if "GET" in allowed:
        allowed.add("HEAD")
    return frozenset(allowed)


def _union(a: frozenset[str] | None, b: frozenset[str] | None) -> frozenset[str] | None:
    if a is None:
        return b
    if b is None:
        return a
    return a | b


def _split_path(path: str) -> list[str]:
    if path == "/":
        return []
    raw = path[1:] if path.startswith("/") else path
    return raw.split("/")


def path_parameter_names(path: str) -> frozenset[str]:
    _, names, _ = _parse_template(path)
    return frozenset(names)


def _parse_template(
    path: str,
) -> tuple[list[tuple[str, bool, str | None]], tuple[str, ...], bool]:
    if not path.startswith("/"):
        raise ValueError(f"Path must start with '/': {path!r}")
    if path != "/" and "//" in path:
        raise ValueError(f"Invalid path template: {path!r}")

    if path == "/":
        return [], (), True

    names: list[str] = []
    segments: list[tuple[str, bool, str | None]] = []
    is_static = True
    raw = path[1:]
    parts = raw.split("/")
    last_index = len(parts) - 1
    for index, part in enumerate(parts):
        if part == "":
            # Trailing slash yields a final empty segment; empty in the middle is invalid.
            if index != last_index or not path.endswith("/"):
                raise ValueError(f"Invalid path template: {path!r}")
            segments.append(("", False, None))
            continue
        m = _PARAM_RE.match(part)
        if m:
            name = m.group(1)
            if not _IDENT_RE.match(name):
                raise ValueError(f"Invalid parameter name: {name!r}")
            if name in names:
                raise ValueError(f"Duplicate parameter name: {name!r}")
            names.append(name)
            segments.append((part, True, name))
            is_static = False
        elif "{" in part or "}" in part:
            raise ValueError(f"Invalid path template segment: {part!r}")
        else:
            segments.append((part, False, None))
    return segments, tuple(names), is_static
