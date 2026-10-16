"""In-process microbenchmarks.

Use ``--quick`` for a single-process timing pass (CI-friendly).
Omit it to run a full pyperf suite.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from benchmarks.apps import (  # noqa: E402
    SCENARIOS,
    build_fastapi,
    build_laminite,
    build_litestar,
    build_starlette,
)


def _scope(method: str, target: str, body: bytes) -> dict[str, Any]:
    parts = urlsplit(target)
    path = parts.path or "/"
    query = parts.query.encode("latin-1")
    headers: list[tuple[bytes, bytes]] = []
    if body:
        headers.append((b"content-type", b"application/json"))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query,
        "headers": headers,
        "client": ("bench", 1),
        "server": ("bench", 80),
        "state": {},
    }


async def _once(app: Any, method: str, target: str, body: bytes) -> None:
    scope = _scope(method, target, body)
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(_message: dict[str, Any]) -> None:
        return None

    await app(scope, receive, send)


def _make_runner(app: Any, scenario: str) -> Any:
    method, target, body = SCENARIOS[scenario]
    loop = asyncio.new_event_loop()

    def run() -> None:
        loop.run_until_complete(_once(app, method, target, body))

    for _ in range(50):
        run()
    return run


def _quick() -> None:
    builders = {
        "laminite": build_laminite,
        "starlette": build_starlette,
        "fastapi": build_fastapi,
        "litestar": build_litestar,
    }
    print("framework.scenario  mean_ns  stdev_ns")
    for framework, builder in builders.items():
        app = builder()
        for scenario in SCENARIOS:
            run = _make_runner(app, scenario)
            samples: list[float] = []
            for _ in range(200):
                start = time.perf_counter_ns()
                run()
                samples.append(time.perf_counter_ns() - start)
            mean = statistics.fmean(samples)
            stdev = statistics.pstdev(samples)
            print(f"{framework}.{scenario}  {mean:.0f}  {stdev:.0f}")


def _pyperf() -> None:
    import pyperf

    runner = pyperf.Runner(values=5, warmups=3)
    runner.metadata["description"] = "Laminite in-process microbenchmarks"
    builders = {
        "laminite": build_laminite,
        "starlette": build_starlette,
        "fastapi": build_fastapi,
        "litestar": build_litestar,
    }
    for framework, builder in builders.items():
        app = builder()
        for scenario in SCENARIOS:
            name = f"{framework}.{scenario}"
            bench = _make_runner(app, scenario)
            runner.bench_func(name, bench)


def main() -> None:
    if "--quick" in sys.argv:
        _quick()
    else:
        _pyperf()


if __name__ == "__main__":
    main()
