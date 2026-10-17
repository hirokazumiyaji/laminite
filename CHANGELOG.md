# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

During 0.x, minor releases may include breaking changes. Breaking changes are called out explicitly.

## [0.2.0] - 2026-10-18

### Added

- `APIRouter` and `include_router` for composable route tables, flattened at freeze time
- `Cookie` parameter binding and multi-value response headers
- `{name:path}` wildcard path parameters
- `StreamingResponse` for sync/async iterators with deferred dependency cleanup (SSE)
- Freeze-time OpenAPI 3.1 document generation served from `openapi_url`
- Per-route `max_body_size` overrides, including through `APIRouter`
- `Request.route` (matched route template) and the `on_unhandled_exception` hook for instrumentation
- `laminite-opentelemetry` workspace package with OpenTelemetry ASGI spans
- Interleaved microbenchmark suite with Starlette-relative and base-branch A/B performance gates

### Changed

- Stricter typing, centralized version handling, and full-workspace coverage in CI
- Fewer hot-path allocations for static, path-parameter, and JSON-body routes
- OpenAPI generation is now lazy when `openapi_url` is unset

### Fixed

- Parameter type resolution for standard scalar types, and preservation of `msgspec.Meta`
- `TestClient` dependency overrides on frozen apps; 413 responses now flow through exception handlers
- CRLF validation for `Set-Cookie` values and attributes
- Cookie parameters are included in OpenAPI, and `StreamingResponse` now matches `Response` for
  headers, cookies, and OpenAPI output

### Breaking

- JSON request bodies now require a JSON `Content-Type`; other requests receive
  `415 Unsupported Media Type`

## [0.1.0] - 2026-10-16

### Added

- Initial release of Laminite, a minimal ASGI framework
- Static-map + segment-trie routing with 404 / 405 / HEAD→GET
- Typed path, query, and header binding via msgspec
- JSON body decoding with `msgspec.Struct` and `Body()`
- Type-keyed dependency injection (app and request scope)
- Lifespan, ASGI middleware, and exception handlers
- Zero-dependency synchronous `TestClient`
