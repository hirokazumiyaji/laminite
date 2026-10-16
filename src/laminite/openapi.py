"""Freeze-time OpenAPI 3.1 document generation via msgspec schemas."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, get_args, get_origin, get_type_hints

import msgspec
from msgspec import Struct

from laminite.compiler import CompiledRoute, RouteInfo
from laminite.response import JSONResponse, Response

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
            op_path = openapi_path(info.path).replace("/", "_").replace("{", "").replace("}", "")
            operation: dict[str, Any] = {
                "operationId": f"{method.lower()}{op_path}_{info.handler.__name__}",
                "parameters": [],
                "responses": {},
            }

            seen_params: set[tuple[str, str]] = set()
            for binding in compiled.bindings:
                param_name: str | None = None
                location: str | None = None
                required = False
                if binding.source == "path":
                    # Path template names are fixed; ignore Annotated aliases.
                    param_name = binding.name
                    location = "path"
                    required = True
                elif binding.source == "query":
                    param_name = binding.alias or binding.name
                    location = "query"
                    required = not binding.has_default
                elif binding.source == "header":
                    param_name = binding.alias or binding.name.replace("_", "-")
                    location = "header"
                    required = not binding.has_default
                elif binding.source == "body" and binding.annotation is not None:
                    operation["requestBody"] = {
                        "required": not binding.has_default,
                        "content": {
                            "application/json": {
                                "schema": _annotation_schema(binding.annotation, components),
                            }
                        },
                    }
                if param_name is None or location is None:
                    continue
                key = (param_name, location)
                if key in seen_params:
                    continue
                seen_params.add(key)
                operation["parameters"].append(
                    {
                        "name": param_name,
                        "in": location,
                        "required": required,
                        "schema": _annotation_schema(binding.annotation, components),
                    }
                )

            status = _response_status(info, return_annotation)
            if return_annotation is type(None) or return_annotation is None:
                description = "No Content" if status == "204" else "Successful Response"
                operation["responses"][status] = {"description": description}
            elif isinstance(return_annotation, type) and issubclass(
                return_annotation, (Response, JSONResponse)
            ):
                # Runtime Response media type/body are not known at freeze time.
                operation["responses"][status] = {"description": "Successful Response"}
            elif return_annotation is str:
                operation["responses"][status] = {
                    "description": "Successful Response",
                    "content": {
                        "text/plain": {"schema": {"type": "string"}},
                    },
                }
            elif return_annotation is bytes:
                operation["responses"][status] = {
                    "description": "Successful Response",
                    "content": {
                        "application/octet-stream": {
                            "schema": {"type": "string", "format": "binary"},
                        },
                    },
                }
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

    def openapi() -> Response:
        return Response(body, media_type="application/json")

    openapi.__name__ = "openapi"
    return openapi
