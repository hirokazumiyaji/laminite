# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

During 0.x, minor releases may include breaking changes. Breaking changes are called out explicitly.

## [0.1.0] - 2026-10-16

### Added

- Initial release of Laminite, a minimal ASGI framework
- Static-map + segment-trie routing with 404 / 405 / HEAD→GET
- Typed path, query, and header binding via msgspec
- JSON body decoding with `msgspec.Struct` and `Body()`
- Type-keyed dependency injection (app and request scope)
- Lifespan, ASGI middleware, and exception handlers
- Zero-dependency synchronous `TestClient`
