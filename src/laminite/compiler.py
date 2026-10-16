"""Handler analysis and RoutePipeline assembly."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from types import UnionType
from typing import (
    Annotated,
    Any,
    NotRequired,
    Required,
    TypedDict,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)
from urllib.parse import parse_qsl

import msgspec
from msgspec import Struct

from laminite.di import DependencyRegistry, ProviderSpec, run_providers, strip_annotated
from laminite.exceptions import HTTPException
from laminite.params import Body, Header, Query
from laminite.request import Request, _Disconnect
from laminite.response import error_json, render_result, send_response

logger = logging.getLogger("laminite")


@dataclass(slots=True)
class RouteInfo:
    path: str
    methods: tuple[str, ...]
    handler: Callable[..., Any]
    status_code: int | None
    offload: bool
    path_param_names: frozenset[str]


@dataclass(slots=True)
class ArgBinding:
    name: str
    source: str  # path | query | header | body | request | dependency
    annotation: Any
    default: Any
    has_default: bool
    alias: str | None = None
    is_list: bool = False
    header_key: bytes | None = None
    dep_type: type | None = None


@dataclass
class CompiledRoute:
    handler: Callable[..., Any]
    is_async: bool
    offload: bool
    status_code: int | None
    bindings: list[ArgBinding]
    params_type: type | None
    query_key_to_name: dict[str, str]  # wire key -> arg name
    list_query_names: frozenset[str]  # arg names that collect lists
    header_keys: dict[bytes, str]  # header bytes -> arg name
    body_binding: ArgBinding | None
    body_decoder: msgspec.json.Decoder[Any] | None
    needs_request: bool
    request_arg_names: tuple[str, ...]
    needs_query: bool
    needs_headers: bool
    request_providers: list[ProviderSpec]
    provided_arg_names: dict[str, type]
    app_values: dict[type[Any], Any] = field(default_factory=dict)
    kw_template: dict[str, Any] = field(default_factory=dict)


def compile_route(
    info: RouteInfo,
    registry: DependencyRegistry,
    *,
    max_body_size: int,
) -> Callable[..., Any]:
    compiled = analyze_handler(info, registry)
    return build_pipeline(compiled, max_body_size=max_body_size)


def analyze_handler(info: RouteInfo, registry: DependencyRegistry) -> CompiledRoute:
    handler = info.handler
    if info.offload and inspect.iscoroutinefunction(handler):
        raise TypeError("offload=True is not allowed on async handlers")

    sig = inspect.signature(handler)
    hints = get_type_hints(handler, include_extras=True)
    bindings: list[ArgBinding] = []
    body_count = 0
    covered_path: set[str] = set()

    for name, param in sig.parameters.items():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            raise TypeError(f"Handler {handler!r} must not use *args")
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            raise TypeError(f"Handler {handler!r} must not use **kwargs")
        if param.kind is inspect.Parameter.POSITIONAL_ONLY:
            raise TypeError(f"Handler {handler!r} must not use positional-only parameters")

        annotation = hints.get(name, str)
        has_default = param.default is not inspect.Parameter.empty
        default = param.default if has_default else None
        source, alias, dep_type = _resolve_source(name, annotation, info.path_param_names, registry)
        if source == "path":
            covered_path.add(name)
            if _is_list_type(annotation):
                raise TypeError(f"Path parameter {name!r} cannot be a list type")
        if source == "body":
            body_count += 1

        stripped = strip_annotated(annotation)
        is_list = source == "query" and _is_list_type(stripped)
        header_key: bytes | None = None
        if source == "header":
            header_name = alias if alias is not None else name.replace("_", "-")
            header_key = header_name.lower().encode("latin-1")

        bindings.append(
            ArgBinding(
                name=name,
                source=source,
                annotation=stripped,
                default=default,
                has_default=has_default,
                alias=alias,
                is_list=is_list,
                header_key=header_key,
                dep_type=dep_type,
            )
        )

    if body_count > 1:
        raise TypeError(f"Handler {handler!r} has more than one body parameter")
    missing = info.path_param_names - covered_path
    if missing:
        raise TypeError(f"Handler {handler!r} missing path parameters: {sorted(missing)}")

    td_fields: dict[str, Any] = {}
    for b in bindings:
        if b.source not in {"path", "query", "header"}:
            continue
        ann = b.annotation
        if b.has_default:
            td_fields[b.name] = NotRequired[ann]  # type: ignore[valid-type]
        else:
            td_fields[b.name] = Required[ann]  # type: ignore[valid-type]

    params_type: type | None = None
    if td_fields:
        params_type = TypedDict(  # type: ignore[misc, valid-type]
            f"_Params_{handler.__name__}_{id(handler)}",
            td_fields,
            total=False,
        )

    body_binding = next((b for b in bindings if b.source == "body"), None)
    body_decoder = None
    if body_binding is not None:
        body_decoder = msgspec.json.Decoder(body_binding.annotation)

    dep_types = {
        b.dep_type for b in bindings if b.source == "dependency" and b.dep_type is not None
    }
    request_providers = registry.resolve_order(dep_types)

    request_arg_names = tuple(b.name for b in bindings if b.source == "request")
    # The body is always read through Request so handlers and providers share one cache.
    needs_request = (
        bool(request_arg_names)
        or body_binding is not None
        or any(p.needs_request for p in request_providers)
    )

    header_keys = {
        b.header_key: b.name for b in bindings if b.source == "header" and b.header_key is not None
    }
    query_key_to_name = {(b.alias or b.name): b.name for b in bindings if b.source == "query"}
    list_query_names = frozenset(b.name for b in bindings if b.source == "query" and b.is_list)

    app_values = dict(registry.app_values())
    kw_template: dict[str, Any] = {}
    provided_arg_names: dict[str, type] = {}
    for b in bindings:
        if b.source == "dependency" and b.dep_type is not None:
            if b.dep_type in app_values:
                kw_template[b.name] = app_values[b.dep_type]
            else:
                provided_arg_names[b.name] = b.dep_type

    return CompiledRoute(
        handler=handler,
        is_async=inspect.iscoroutinefunction(handler),
        offload=info.offload,
        status_code=info.status_code,
        bindings=bindings,
        params_type=params_type,
        query_key_to_name=query_key_to_name,
        list_query_names=list_query_names,
        header_keys=header_keys,
        body_binding=body_binding,
        body_decoder=body_decoder,
        needs_request=needs_request,
        request_arg_names=request_arg_names,
        needs_query=any(b.source == "query" for b in bindings),
        needs_headers=any(b.source == "header" for b in bindings),
        request_providers=request_providers,
        provided_arg_names=provided_arg_names,
        app_values=app_values,
        kw_template=kw_template,
    )


def build_pipeline(
    route: CompiledRoute,
    *,
    max_body_size: int,
) -> Callable[..., Any]:
    handler = route.handler
    params_type = route.params_type
    body_binding = route.body_binding
    body_decoder = route.body_decoder
    status_code_opt = route.status_code

    async def pipeline(
        scope: dict[str, Any],
        receive: Any,
        send: Any,
        *,
        path_params: dict[str, str],
        is_head: bool,
        exception_handlers: Mapping[type, Callable[..., Any]],
    ) -> None:
        request: Request | None = None
        kwargs = dict(route.kw_template)

        try:
            if route.needs_request:
                request = Request(
                    scope, receive, path_params=path_params, max_body_size=max_body_size
                )
                for name in route.request_arg_names:
                    kwargs[name] = request

            if params_type is not None:
                values: dict[str, Any] = {}
                for b in route.bindings:
                    if b.source == "path":
                        values[b.name] = path_params[b.name]
                if route.needs_query:
                    raw_q = parse_qsl(
                        scope.get("query_string", b"").decode("latin-1"),
                        keep_blank_values=True,
                    )
                    for key, value in raw_q:
                        arg_name = route.query_key_to_name.get(key)
                        if arg_name is None:
                            continue
                        if arg_name in route.list_query_names:
                            values.setdefault(arg_name, []).append(value)
                        else:
                            values[arg_name] = value
                if route.needs_headers:
                    wanted = route.header_keys
                    for key, value in scope.get("headers", []):
                        name = wanted.get(key)
                        if name is not None:
                            values[name] = value.decode("latin-1")

                try:
                    converted = msgspec.convert(values, params_type, strict=False)
                except msgspec.ValidationError as exc:
                    await _send_error(send, str(exc), 422, is_head)
                    return
                kwargs.update(converted)  # type: ignore[arg-type]

            if body_binding is not None and body_decoder is not None:
                assert request is not None
                try:
                    raw = await request.body()
                except HTTPException as exc:
                    await _send_http_exception(send, exc, is_head)
                    return
                if not raw and body_binding.has_default:
                    kwargs[body_binding.name] = body_binding.default
                else:
                    try:
                        kwargs[body_binding.name] = body_decoder.decode(raw)
                    except msgspec.ValidationError as exc:
                        await _send_error(send, str(exc), 422, is_head)
                        return
                    except msgspec.DecodeError as exc:
                        await _send_error(send, str(exc), 400, is_head)
                        return

            stack: AsyncExitStack | None = None
            try:
                if route.request_providers:
                    stack = AsyncExitStack()
                    await stack.__aenter__()
                    dep_values = await run_providers(
                        route.request_providers,
                        app_values=route.app_values,
                        request=request,
                        stack=stack,
                    )
                    for name, tp in route.provided_arg_names.items():
                        kwargs[name] = dep_values[tp]

                if route.offload:
                    result = await asyncio.to_thread(handler, **kwargs)
                elif route.is_async:
                    result = await handler(**kwargs)
                else:
                    result = handler(**kwargs)

                # Render before successful cleanup so encode failures can roll back.
                status_out, headers, body = render_result(
                    result, status_code=_resolve_status(status_code_opt, result)
                )
            except BaseException as exc:
                if stack is not None:
                    # Re-raise even if a provider suppresses, so a failure or a cancellation
                    # is never turned into a success response.
                    try:
                        await stack.__aexit__(type(exc), exc, exc.__traceback__)
                    except BaseException as cleanup_exc:
                        raise cleanup_exc from exc
                raise
            else:
                if stack is not None:
                    await stack.__aexit__(None, None, None)

            await send_response(
                send, status_code=status_out, headers=headers, body=body, is_head=is_head
            )
        except _Disconnect:
            return
        except Exception as exc:
            exc_handler = _find_exception_handler(exception_handlers, exc)
            if exc_handler is not None:
                if request is None:
                    request = Request(
                        scope, receive, path_params=path_params, max_body_size=max_body_size
                    )
                status_out, headers, body = await _run_exception_handler(exc_handler, request, exc)
            elif isinstance(exc, HTTPException):
                await _send_http_exception(send, exc, is_head)
                return
            else:
                logger.exception("Unhandled exception in handler")
                status_out, headers, body = error_json("Internal Server Error", 500)
            await send_response(
                send, status_code=status_out, headers=headers, body=body, is_head=is_head
            )

    return pipeline


def _resolve_status(status_code: int | None, result: Any) -> int:
    if status_code is not None:
        return status_code
    if result is None:
        return 204
    return 200


async def _send_error(send: Any, detail: Any, status_code: int, is_head: bool) -> None:
    status, headers, body = error_json(detail, status_code)
    await send_response(send, status_code=status, headers=headers, body=body, is_head=is_head)


async def _send_http_exception(send: Any, exc: HTTPException, is_head: bool) -> None:
    status, headers, body = error_json(exc.detail, exc.status_code)
    if exc.headers:
        for key, value in exc.headers.items():
            headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))
    await send_response(send, status_code=status, headers=headers, body=body, is_head=is_head)


def _find_exception_handler(
    handlers: Mapping[type, Callable[..., Any]], exc: BaseException
) -> Callable[..., Any] | None:
    for cls in type(exc).mro():
        handler = handlers.get(cls)
        if handler is not None:
            return handler
    return None


async def _run_exception_handler(
    handler: Callable[..., Any], request: Request, exc: BaseException
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    try:
        result = handler(request, exc)
        if inspect.isawaitable(result):
            result = await result
        return render_result(result, status_code=200)
    except Exception:
        logger.exception("Exception handler failed")
        return error_json("Internal Server Error", 500)


def _resolve_source(
    name: str,
    annotation: Any,
    path_params: frozenset[str],
    registry: DependencyRegistry,
) -> tuple[str, str | None, type | None]:
    if name in path_params:
        return "path", None, None

    raw = annotation
    alias: str | None = None
    marker_source: str | None = None
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        raw = args[0]
        for meta in args[1:]:
            if isinstance(meta, Header):
                marker_source = "header"
                alias = meta.alias
            elif isinstance(meta, Query):
                marker_source = "query"
                alias = meta.alias
            elif isinstance(meta, Body):
                marker_source = "body"

    if raw is Request:
        return "request", None, None
    if marker_source is not None:
        return marker_source, alias, None
    if isinstance(raw, type) and registry.has(raw):
        return "dependency", None, raw
    if isinstance(raw, type) and issubclass(raw, Struct):
        return "body", None, None
    if isinstance(raw, type) and not _is_query_compatible(raw):
        # User-defined class that is not a registered dependency.
        raise ValueError(f"Unregistered dependency: {raw!r}")
    return "query", alias, None


def _is_list_type(annotation: Any) -> bool:
    annotation = _unwrap_optional(annotation)
    return get_origin(annotation) is list


def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _is_query_compatible(annotation: Any) -> bool:
    annotation = _unwrap_optional(annotation)
    if annotation in (str, int, float, bool, bytes):
        return True
    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        return bool(args) and _is_query_compatible(args[0])
    if origin is Union or origin is UnionType:
        return all(_is_query_compatible(a) for a in get_args(annotation) if a is not type(None))
    return False
