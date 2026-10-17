"""Freeze-time OpenAPI 3.1 document generation via msgspec schemas."""

from __future__ import annotations

from typing import Any, get_type_hints

import msgspec

from laminite.compiler import CompiledRoute, RouteInfo
from laminite.response import Response

_REF_TEMPLATE = "#/components/schemas/{name}"


def _annotation_schema(annotation: Any, components: dict[str, Any]) -> dict[str, Any]:
    if annotation is Any or annotation is object:
        return {}
    try:
        schemas, comps = msgspec.json.schema_components([annotation], ref_template=_REF_TEMPLATE)
    except (TypeError, ValueError):
        return {"type": "string"}
    components.update(comps)
    return schemas[0]


def _response_spec(
    info: RouteInfo,
    return_annotation: Any,
    components: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    is_none = return_annotation is type(None)
    status = (
        str(info.status_code) if info.status_code is not None else ("204" if is_none else "200")
    )

    if is_none:
        description = "No Content" if status == "204" else "Successful Response"
        return status, {"description": description}
    if isinstance(return_annotation, type) and issubclass(return_annotation, Response):
        return status, {"description": "Successful Response"}
    if return_annotation is str:
        content = {"text/plain": {"schema": {"type": "string"}}}
    elif return_annotation is bytes:
        content = {
            "application/octet-stream": {
                "schema": {"type": "string", "format": "binary"},
            },
        }
    else:
        content = {
            "application/json": {
                "schema": _annotation_schema(return_annotation, components),
            },
        }
    return status, {"description": "Successful Response", "content": content}


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
        path_item = paths.setdefault(info.path, {})
        hints = get_type_hints(info.handler, include_extras=True)
        return_annotation = hints.get("return", Any)
        op_slug = info.path.replace("/", "_").replace("{", "").replace("}", "")

        path_bindings = {b.name: b for b in compiled.bindings if b.source == "path"}
        parameters: list[dict[str, Any]] = []
        for path_name in sorted(info.path_param_names):
            parameters.append(
                {
                    "name": path_name,
                    "in": "path",
                    "required": True,
                    "schema": _annotation_schema(path_bindings[path_name].annotation, components),
                }
            )

        request_body: dict[str, Any] | None = None
        for binding in compiled.bindings:
            if binding.source == "query":
                param_name = binding.alias or binding.name
                location = "query"
            elif binding.source == "header":
                param_name = binding.alias or binding.name.replace("_", "-")
                location = "header"
            elif binding.source == "body" and binding.annotation is not None:
                request_body = {
                    "required": not binding.has_default,
                    "content": {
                        "application/json": {
                            "schema": _annotation_schema(binding.annotation, components),
                        }
                    },
                }
                continue
            else:
                continue
            parameters.append(
                {
                    "name": param_name,
                    "in": location,
                    "required": not binding.has_default,
                    "schema": _annotation_schema(binding.annotation, components),
                }
            )

        status, response = _response_spec(info, return_annotation, components)

        for method in info.methods:
            operation: dict[str, Any] = {
                "operationId": f"{method.lower()}{op_slug}_{info.handler.__name__}",
                "parameters": parameters,
                "responses": {status: response},
            }
            if request_body is not None:
                operation["requestBody"] = request_body
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
