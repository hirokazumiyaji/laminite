"""Freeze-time OpenAPI 3.1 document generation via msgspec schemas."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, get_args, get_origin, get_type_hints

import msgspec
from msgspec import Struct

from laminite.compiler import CompiledRoute, RouteInfo

_PATH_PARAM_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::path)?\}")
_REF_TEMPLATE = "#/components/schemas/{name}"


def openapi_path(path: str) -> str:
    """Convert a Laminite path template to an OpenAPI path template."""
    return _PATH_PARAM_RE.sub(r"{\1}", path)


def _annotation_schema(annotation: Any, components: dict[str, Any]) -> dict[str, Any]:
    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        item = args[0] if args else Any
        return {"type": "array", "items": _annotation_schema(item, components)}
    if annotation is Any or annotation is object:
        return {}
    if isinstance(annotation, type) and issubclass(annotation, Struct):
        schemas, comps = msgspec.json.schema_components(
            [annotation], ref_template=_REF_TEMPLATE
        )
        components.update(comps)
        return schemas[0]
    try:
        schema = msgspec.json.schema(annotation)
    except (TypeError, ValueError):
        return {"type": "string"}
    # Inline $defs from single-type schema into components when present.
    defs = schema.pop("$defs", None)
    if isinstance(defs, dict):
        for name, defn in defs.items():
            components.setdefault(name, defn)
        if "$ref" in schema:
            ref_name = schema["$ref"].rsplit("/", 1)[-1]
            schema = {"$ref": _REF_TEMPLATE.format(name=ref_name)}
    return schema  # type: ignore[no-any-return]


def _response_status(info: RouteInfo, return_annotation: Any) -> str:
    if info.status_code is not None:
        return str(info.status_code)
    if return_annotation is type(None) or return_annotation is None:
        return "204"
    return "200"


def build_openapi(
    routes: list[tuple[RouteInfo, CompiledRoute]],
    *,
    title: str,
    version: str,
) -> dict[str, Any]:
    """Build an OpenAPI 3.1 document from freeze-time route analysis."""
    paths: dict[str, Any] = {}
    components: dict[str, Any] = {}

    for info, compiled in routes:
        path_item = paths.setdefault(openapi_path(info.path), {})
        hints = get_type_hints(info.handler, include_extras=True)
        return_annotation = hints.get("return", Any)

        for method in info.methods:
            operation: dict[str, Any] = {
                "operationId": f"{info.handler.__name__}_{method.lower()}",
                "parameters": [],
                "responses": {},
            }

            for binding in compiled.bindings:
                if binding.source == "path":
                    operation["parameters"].append(
                        {
                            "name": binding.alias or binding.name,
                            "in": "path",
                            "required": True,
                            "schema": _annotation_schema(binding.annotation, components),
                        }
                    )
                elif binding.source == "query":
                    operation["parameters"].append(
                        {
                            "name": binding.alias or binding.name,
                            "in": "query",
                            "required": not binding.has_default,
                            "schema": _annotation_schema(binding.annotation, components),
                        }
                    )
                elif binding.source == "header":
                    name = binding.alias or binding.name.replace("_", "-")
                    operation["parameters"].append(
                        {
                            "name": name,
                            "in": "header",
                            "required": not binding.has_default,
                            "schema": _annotation_schema(binding.annotation, components),
                        }
                    )
                elif binding.source == "body" and binding.annotation is not None:
                    operation["requestBody"] = {
                        "required": not binding.has_default,
                        "content": {
                            "application/json": {
                                "schema": _annotation_schema(binding.annotation, components),
                            }
                        },
                    }

            status = _response_status(info, return_annotation)
            if status == "204":
                operation["responses"][status] = {"description": "No Content"}
            else:
                operation["responses"][status] = {
                    "description": "Successful Response",
                    "content": {
                        "application/json": {
                            "schema": _annotation_schema(return_annotation, components),
                        }
                    },
                }

            path_item[method.lower()] = operation

    doc: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": title, "version": version},
        "paths": paths,
    }
    if components:
        doc["components"] = {"schemas": components}
    return doc


def encode_openapi(doc: dict[str, Any]) -> bytes:
    return msgspec.json.encode(doc)


def make_openapi_handler(body: bytes) -> Callable[..., Any]:
    """Return a sync handler that serves a pre-encoded OpenAPI document."""

    def openapi() -> bytes:
        return body

    openapi.__name__ = "openapi"
    return openapi
