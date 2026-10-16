"""Static-map + segment-trie router."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeVar

V = TypeVar("V")

_PARAM_RE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_PATH_PARAM_RE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*):path\}$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Read-only shared empty path-params for prebuilt static Matches: a mutable
# singleton here would leak writes across every static resolve.
_EMPTY_PARAMS: Mapping[str, str] = MappingProxyType({})


class NotFound(Exception):
    """No route matched the path."""


@dataclass(slots=True)
class MethodNotAllowed(Exception):
    allowed: frozenset[str]


@dataclass(slots=True)
class Match[V]:
    value: V
    path_params: Mapping[str, str]
    is_head: bool = False


class _Node[V]:
    __slots__ = (
        "methods",
        "param",
        "param_name",
        "param_names",
        "path",
        "path_param_name",
        "static",
    )

    def __init__(self) -> None:
        self.static: dict[str, _Node[V]] = {}
        self.param: _Node[V] | None = None
        self.param_name: str | None = None
        self.path: _Node[V] | None = None
        self.path_param_name: str | None = None
        self.methods: dict[str, V] = {}
        self.param_names: tuple[str, ...] = ()


class Router[V]:
    def __init__(self) -> None:
        self._static: dict[str, dict[str, V]] = {}
        # Prebuilt Matches for static paths (includes HEAD→GET fallback entries).
        self._static_matches: dict[str, dict[str, Match[V]]] = {}
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
            for segment, kind, name in segments:
                if kind == "path":
                    assert name is not None
                    if node.path is None:
                        node.path = _Node()
                        node.path_param_name = name
                    elif node.path_param_name != name:
                        raise ValueError(
                            f"Conflicting path parameter names at the same position: "
                            f"{node.path_param_name!r} vs {name!r}"
                        )
                    node = node.path
                elif kind == "param":
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
                    self._static_matches.pop(path, None)
                else:
                    self._rebuild_static_matches(path)
            raise

        if is_static:
            self._rebuild_static_matches(path)

    def _rebuild_static_matches(self, path: str) -> None:
        methods = self._static[path]
        matches: dict[str, Match[V]] = {
            method: Match(value, _EMPTY_PARAMS) for method, value in methods.items()
        }
        if "GET" in methods and "HEAD" not in methods:
            matches["HEAD"] = Match(methods["GET"], _EMPTY_PARAMS, is_head=True)
        self._static_matches[path] = matches

    def resolve(self, method: str, path: str) -> Match[V]:
        method = method.upper()
        static_matches = self._static_matches.get(path)
        if static_matches is not None:
            hit = static_matches.get(method)
            if hit is not None:
                return hit

        parts = _split_path(path)
        match, allowed = self._search(self._root, parts, 0, [], method)
        if match is not None:
            return match
        if allowed is not None:
            raise MethodNotAllowed(allowed)
        static_methods = self._static.get(path)
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
            allowed: frozenset[str] | None = None
            if node.methods:
                hit = _match_method(node, values, method)
                if hit is not None:
                    return hit, None
                allowed = _allow_set(node.methods)
            if node.path is not None:
                values.append("")
                hit = _match_method(node.path, values, method)
                values.pop()
                # Path-capture must not override a more-specific leaf for this path.
                if hit is not None and allowed is None:
                    return hit, None
                if node.path.methods:
                    allowed = _union(allowed, _allow_set(node.path.methods))
            return None, allowed

        segment = parts[index]
        allowed = None

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

        if node.path is not None:
            values.append("/".join(parts[index:]))
            hit = _match_method(node.path, values, method)
            values.pop()
            # If a static/param leaf already owns this path, keep 405 from that leaf
            # instead of letting {name:path} absorb the request.
            if hit is not None and allowed is None:
                return hit, None
            if node.path.methods:
                allowed = _union(allowed, _allow_set(node.path.methods))

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
) -> tuple[list[tuple[str, str, str | None]], tuple[str, ...], bool]:
    """Parse a path template into (segment, kind, name) triples.

    ``kind`` is ``"static"``, ``"param"``, or ``"path"``.
    """
    if not path.startswith("/"):
        raise ValueError(f"Path must start with '/': {path!r}")
    if path != "/" and "//" in path:
        raise ValueError(f"Invalid path template: {path!r}")

    if path == "/":
        return [], (), True

    names: list[str] = []
    segments: list[tuple[str, str, str | None]] = []
    is_static = True
    raw = path[1:]
    parts = raw.split("/")
    last_index = len(parts) - 1
    for index, part in enumerate(parts):
        if part == "":
            # Trailing slash yields a final empty segment; empty in the middle is invalid.
            if index != last_index or not path.endswith("/"):
                raise ValueError(f"Invalid path template: {path!r}")
            segments.append(("", "static", None))
            continue
        m_path = _PATH_PARAM_RE.match(part)
        if m_path:
            if index != last_index:
                raise ValueError(f"{{name:path}} must be the final segment: {path!r}")
            name = m_path.group(1)
            if not _IDENT_RE.match(name):
                raise ValueError(f"Invalid parameter name: {name!r}")
            if name in names:
                raise ValueError(f"Duplicate parameter name: {name!r}")
            names.append(name)
            segments.append((part, "path", name))
            is_static = False
            continue
        m = _PARAM_RE.match(part)
        if m:
            name = m.group(1)
            if not _IDENT_RE.match(name):
                raise ValueError(f"Invalid parameter name: {name!r}")
            if name in names:
                raise ValueError(f"Duplicate parameter name: {name!r}")
            names.append(name)
            segments.append((part, "param", name))
            is_static = False
        elif "{" in part or "}" in part:
            raise ValueError(f"Invalid path template segment: {part!r}")
        else:
            segments.append((part, "static", None))
    return segments, tuple(names), is_static
