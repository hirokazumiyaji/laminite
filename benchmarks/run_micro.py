"""In-process microbenchmarks with interleaved rounds and CI gates.

Each round measures every target once per scenario, rotating the target order so
clock drift and other-runner noise hit all targets equally. Reported numbers are
medians of the per-round samples, and gates compare medians of per-round ratios.

Usage::

    uv run --group bench python -m benchmarks.run_micro            # compare frameworks
    uv run --group bench python -m benchmarks.run_micro --quick    # shorter CI run
    uv run --group bench python -m benchmarks.run_micro --gate     # fail if S1-S5 lose to Starlette
    # A/B regression gate vs a git ref:
    uv run --group bench python -m benchmarks.run_micro --compare-ref origin/main
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Generator, Iterable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Set from --laminite-src before any builder imports laminite, so the A/B
# worker loads the framework from the requested checkout.
_laminite_src: Path | None = None

from benchmarks.apps import (  # noqa: E402
    BASE_SCENARIOS,
    SCENARIOS,
    BenchApp,
    build_fastapi,
    build_laminite,
    build_laminite_base,
    build_litestar,
    build_starlette,
)

BUILDERS: dict[str, Callable[[], BenchApp]] = {
    "laminite": build_laminite,
    "starlette": build_starlette,
    "fastapi": build_fastapi,
    "litestar": build_litestar,
}

QUICK_ROUNDS = 25
QUICK_ITERATIONS = 500
DEFAULT_ROUNDS = 31
DEFAULT_ITERATIONS = 1000
WARMUP_ROUNDS = 2
DEFAULT_MAX_REGRESSION = 5.0
_WORKER_QUIT = "__quit__"
_WORKER_TIMEOUT = 300.0

Send = Callable[[dict[str, Any]], Awaitable[None]]


async def _noop_send(_message: dict[str, Any]) -> None:
    return None


@dataclass(slots=True)
class Case:
    """A prebuilt scope plus reusable receive/send callbacks for one target."""

    app: Any
    scope: dict[str, Any]
    message: dict[str, Any]
    send: Send
    _consumed: bool = False

    async def _receive(self) -> dict[str, Any]:
        if self._consumed:
            return {"type": "http.disconnect"}
        self._consumed = True
        return self.message

    async def run(self, iterations: int) -> None:
        app = self.app
        scope = self.scope
        receive = self._receive
        send = self.send
        for _ in range(iterations):
            self._consumed = False
            await app(scope, receive, send)


@dataclass(slots=True)
class TargetResult:
    median_ns: float
    stdev_ns: float


def make_case(bench: BenchApp, scenario_name: str) -> Case:
    scenario = SCENARIOS[scenario_name]
    parts = urlsplit(scenario.target)
    path = parts.path or "/"
    headers = list(scenario.headers)
    if scenario.body:
        headers.append((b"content-type", b"application/json"))
        headers.append((b"content-length", str(len(scenario.body)).encode("latin-1")))
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": scenario.method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": parts.query.encode("latin-1"),
        "headers": headers,
        "client": ("bench", 1),
        "server": ("bench", 80),
        "state": {},
    }
    message = {"type": "http.request", "body": scenario.body, "more_body": False}
    return Case(bench.app, scope, message, _noop_send)


def measure_round(loop: asyncio.AbstractEventLoop, case: Case, iterations: int) -> float:
    start = time.perf_counter_ns()
    loop.run_until_complete(case.run(iterations))
    return (time.perf_counter_ns() - start) / iterations


def summarize(samples: list[float]) -> TargetResult:
    # The median of per-round means is robust to a few noisy rounds on shared runners.
    return TargetResult(statistics.median(samples), statistics.pstdev(samples))


def paired_ratio(numerator: list[float], denominator: list[float]) -> float | None:
    """Median of per-round ratios; cancels global drift between rounds."""
    if not numerator or len(numerator) != len(denominator):
        return None
    return statistics.median([n / d for n, d in zip(numerator, denominator, strict=True)])


def _warmup(loop: asyncio.AbstractEventLoop, cases: Iterable[Case], iterations: int) -> None:
    for case in cases:
        for _ in range(WARMUP_ROUNDS):
            loop.run_until_complete(case.run(iterations))


def run_interleaved(
    cases: dict[tuple[str, str], Case],
    *,
    rounds: int,
    iterations: int,
) -> dict[tuple[str, str], list[float]]:
    """Measure every (target, scenario) once per round, rotating target order."""
    loop = asyncio.new_event_loop()
    try:
        _warmup(loop, cases.values(), iterations)
        samples: dict[tuple[str, str], list[float]] = {key: [] for key in cases}
        scenarios = [name for name in SCENARIOS if any(key[1] == name for key in cases)]
        for index in range(rounds):
            for scenario in scenarios:
                targets = [target for target in BUILDERS if (target, scenario) in cases]
                if not targets:
                    continue
                start = index % len(targets)
                for target in targets[start:] + targets[:start]:
                    key = (target, scenario)
                    samples[key].append(measure_round(loop, cases[key], iterations))
        return samples
    finally:
        loop.close()


def collect_framework_results(*, rounds: int, iterations: int) -> dict[str, dict[str, list[float]]]:
    cases: dict[tuple[str, str], Case] = {}
    for target, builder in BUILDERS.items():
        try:
            bench = builder()
        except ImportError as exc:
            print(f"skipping {target}: {exc}", file=sys.stderr)
            continue
        except (AttributeError, TypeError, ValueError) as exc:
            # Optional targets may be importable but incompatible; laminite and
            # starlette are required and their failure is reported by `missing`.
            if target in {"laminite", "starlette"}:
                raise
            print(f"skipping {target}: {exc}", file=sys.stderr)
            continue
        for scenario in bench.scenarios:
            cases[(target, scenario)] = make_case(bench, scenario)

    targets = {target for target, _ in cases}
    missing = {"laminite", "starlette"} - targets
    if missing:
        raise RuntimeError(
            f"required benchmark target(s) unavailable: {', '.join(sorted(missing))}"
        )

    samples = run_interleaved(cases, rounds=rounds, iterations=iterations)
    results: dict[str, dict[str, list[float]]] = {}
    for (target, scenario), values in samples.items():
        results.setdefault(target, {})[scenario] = values
    return results


def _worker_cases(
    loop: asyncio.AbstractEventLoop, iterations: int
) -> tuple[dict[str, Case], list[str]]:
    """Build the widest scenario set the imported laminite supports."""
    warnings: list[str] = []
    last_exc: Exception | None = None
    for builder in (build_laminite, build_laminite_base):
        try:
            bench = builder()
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            # Only a builder for an older ref is expected to fail here; harness
            # errors below must propagate instead of silently shrinking the set.
            last_exc = exc
            warnings.append(f"{builder.__name__} unavailable: {exc}")
            continue
        cases = {name: make_case(bench, name) for name in bench.scenarios}
        _warmup(loop, cases.values(), iterations)
        return cases, warnings
    assert last_exc is not None
    raise last_exc


def run_worker(iterations: int) -> int:
    """Measurement worker used by ``--compare-ref``; measures on stdin requests."""
    import laminite

    if _laminite_src is not None:
        loaded = Path(laminite.__file__ or "").resolve()
        if not loaded.is_relative_to(_laminite_src):
            print(
                f"worker loaded laminite from {loaded}, expected under {_laminite_src}",
                file=sys.stderr,
            )
            return 2

    loop = asyncio.new_event_loop()
    try:
        cases, warnings = _worker_cases(loop, iterations)
        print(
            json.dumps({"ready": True, "scenarios": sorted(cases), "warnings": warnings}),
            flush=True,
        )
        for line in sys.stdin:
            name = line.strip()
            if not name:
                continue
            if name == _WORKER_QUIT:
                break
            case = cases.get(name)
            if case is None:
                print(json.dumps({"error": f"unknown scenario: {name}"}), flush=True)
                continue
            median_ns = measure_round(loop, case, iterations)
            print(json.dumps({"scenario": name, "median_ns": median_ns}), flush=True)
    finally:
        loop.close()
    return 0


class _WorkerSession:
    """A long-lived measurement worker, used symmetrically for head and base."""

    def __init__(self, harness_root: Path, src_path: Path, python: Path, iterations: int) -> None:
        # `-m benchmarks.run_micro` always resolves to `harness_root` (this
        # checkout); --laminite-src only redirects the `laminite` import into the
        # measured tree, so the worker protocol never depends on the other ref.
        # The harness itself must only import dependencies that every ref's bench
        # group provides (msgspec and pydantic), since the baseline worker runs
        # with the baseline lockfile's environment.
        self._proc: subprocess.Popen[str] | None = None
        # Lifetime is tied to this session; close() handles cleanup on every path.
        self._stderr_file = tempfile.TemporaryFile(mode="w+")  # noqa: SIM115
        self._reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bench-worker")
        self._closed = False
        try:
            self._proc = subprocess.Popen(
                [
                    str(python),
                    "-m",
                    "benchmarks.run_micro",
                    "--worker",
                    "--laminite-src",
                    str(src_path),
                    "--iterations",
                    str(iterations),
                ],
                cwd=harness_root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr_file,
                text=True,
            )
            payload = self._read_json(_WORKER_TIMEOUT)
        except BaseException:
            self.close()
            raise
        if not payload.get("ready") or "scenarios" not in payload:
            self.close()
            raise RuntimeError(f"measurement worker did not become ready: {payload}")
        self.scenarios = frozenset(payload["scenarios"])
        for warning in payload.get("warnings", []):
            print(f"worker warning: {warning}", file=sys.stderr)

    def _readline(self) -> str:
        assert self._proc is not None and self._proc.stdout is not None
        return self._proc.stdout.readline()

    def _stderr_text(self) -> str:
        self._stderr_file.flush()
        self._stderr_file.seek(0)
        return self._stderr_file.read()

    def _read_json(self, timeout: float) -> dict[str, Any]:
        future = self._reader.submit(self._readline)
        try:
            line = future.result(timeout=timeout)
        except FutureTimeoutError as exc:
            raise RuntimeError(f"measurement worker timed out after {timeout:.0f}s") from exc
        if not line:
            code = self._proc.poll() if self._proc is not None else "?"
            raise RuntimeError(f"measurement worker exited ({code}): {self._stderr_text().strip()}")
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid worker response: {line!r}") from exc

    def measure(self, scenario: str) -> float:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("measurement worker is not running")
        proc.stdin.write(f"{scenario}\n")
        proc.stdin.flush()
        payload = self._read_json(_WORKER_TIMEOUT)
        if "error" in payload:
            raise RuntimeError(payload["error"])
        try:
            return float(payload["median_ns"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid worker response: {payload!r}") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                if proc.stdin is not None:
                    proc.stdin.write(f"{_WORKER_QUIT}\n")
                    proc.stdin.flush()
                    proc.stdin.close()
                proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
                proc.wait()
        if proc is not None:
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
        self._stderr_file.close()
        self._reader.shutdown(wait=True)


def _prepare_baseline_env(checkout: Path) -> Path:
    """Sync a venv from the ref's own lockfile so A/B uses its dependency set."""
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv not found; cannot prepare the baseline environment")
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    synced = subprocess.run(
        [
            uv,
            "sync",
            "--group",
            "bench",
            "--no-install-project",
            "--python",
            version,
            "--quiet",
        ],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
    )
    if synced.returncode != 0:
        raise RuntimeError(f"uv sync for the baseline ref failed: {synced.stderr.strip()}")
    scripts = "Scripts" if os.name == "nt" else "bin"
    python = checkout / ".venv" / scripts / ("python.exe" if os.name == "nt" else "python")
    if not python.exists():
        raise RuntimeError(f"baseline interpreter not found: {python}")
    return python


def _check_interpreter(python: Path, label: str) -> None:
    """Fail loudly if the worker interpreter cannot import benchmark deps."""
    probe = subprocess.run(
        [str(python), "-c", "import msgspec, pydantic"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        raise RuntimeError(
            f"{label} interpreter {python} cannot import benchmark dependencies: "
            f"{probe.stderr.strip()}"
        )


@contextmanager
def _baseline_checkout(ref: str) -> Generator[Path]:
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
    )
    if resolved.returncode != 0:
        raise RuntimeError(f"unknown compare ref: {ref}")

    temp_dir = Path(tempfile.mkdtemp(prefix="laminite-ab-"))
    checkout = temp_dir / "baseline"
    added = subprocess.run(
        ["git", "worktree", "add", "--detach", str(checkout), ref],
        cwd=_ROOT,
        capture_output=True,
        text=True,
    )
    if added.returncode != 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError(f"git worktree add failed: {added.stderr.strip()}")
    try:
        yield checkout
    finally:
        # Untracked environment files must not keep the worktree registered.
        shutil.rmtree(checkout / ".venv", ignore_errors=True)
        removed = subprocess.run(
            ["git", "worktree", "remove", "--force", str(checkout)],
            cwd=_ROOT,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(temp_dir, ignore_errors=True)
        if removed.returncode != 0:
            # The directory is gone now, so prune can drop a stale registration.
            print(f"git worktree remove failed: {removed.stderr.strip()}", file=sys.stderr)
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=_ROOT,
                capture_output=True,
                text=True,
            )


def run_ab(
    ref: str,
    *,
    rounds: int,
    iterations: int,
    max_regression: float,
) -> tuple[dict[str, tuple[TargetResult, TargetResult, float]], list[str]]:
    with _baseline_checkout(ref) as checkout:
        # Both sides run in their own worker process so scheduling behavior is
        # symmetric; the parent only ping-pongs requests between them.
        head_worker: _WorkerSession | None = None
        base_worker: _WorkerSession | None = None
        try:
            baseline_python = _prepare_baseline_env(checkout)
            head_python = Path(sys.executable)
            _check_interpreter(head_python, "current")
            _check_interpreter(baseline_python, "baseline")
            head_worker = _WorkerSession(_ROOT, _ROOT / "src", head_python, iterations)
            if head_worker.scenarios != frozenset(SCENARIOS):
                missing = sorted(set(SCENARIOS) - head_worker.scenarios)
                raise RuntimeError(f"current ref is missing scenarios: {', '.join(missing)}")
            base_worker = _WorkerSession(_ROOT, checkout / "src", baseline_python, iterations)
            names = [
                name
                for name in SCENARIOS
                if name in head_worker.scenarios and name in base_worker.scenarios
            ]
            skipped = [name for name in SCENARIOS if name not in names]
            if skipped:
                print(
                    "skipping A/B scenarios not supported by both sides: " + ", ".join(skipped),
                    file=sys.stderr,
                )
            if not names:
                raise RuntimeError("no common scenarios for A/B comparison")
            samples: dict[str, tuple[list[float], list[float]]] = {name: ([], []) for name in names}
            for index in range(rounds):
                start = index % len(names)
                for name in names[start:] + names[:start]:
                    if index % 2 == 0:
                        head_ns = head_worker.measure(name)
                        base_ns = base_worker.measure(name)
                    else:
                        base_ns = base_worker.measure(name)
                        head_ns = head_worker.measure(name)
                    samples[name][0].append(base_ns)
                    samples[name][1].append(head_ns)
        finally:
            if base_worker is not None:
                base_worker.close()
            if head_worker is not None:
                head_worker.close()

    results: dict[str, tuple[TargetResult, TargetResult, float]] = {}
    failures: list[str] = []
    for name, (base_samples, head_samples) in samples.items():
        base = summarize(base_samples)
        head_result = summarize(head_samples)
        ratio = paired_ratio(head_samples, base_samples)
        assert ratio is not None
        delta = (ratio - 1) * 100
        results[name] = (base, head_result, delta)
        if delta > max_regression:
            failures.append(f"{name}: {delta:+.1f}% regression (threshold +{max_regression:.1f}%)")
    return results, failures


def _gate_failures(results: dict[str, dict[str, list[float]]]) -> list[str]:
    failures: list[str] = []
    for scenario in BASE_SCENARIOS:
        ratio = paired_ratio(
            results.get("laminite", {}).get(scenario, []),
            results.get("starlette", {}).get(scenario, []),
        )
        if ratio is None:
            failures.append(f"{scenario}: missing measurement")
        elif ratio >= 1.0:
            failures.append(f"{scenario}: laminite/starlette ratio {ratio:.2f} >= 1.00")
    return failures


def render_comparison(
    results: dict[str, dict[str, list[float]]], *, failed: set[str], gated: bool
) -> str:
    targets = list(BUILDERS)
    header = (
        "| Scenario | "
        + " | ".join(f"{target} ns" for target in targets)
        + " | Laminite / Starlette | Status |"
    )
    separator = "|---|" + "---:|" * len(targets) + "---:|---|"
    lines = [header, separator]
    for scenario in SCENARIOS:
        cells: list[str] = []
        for target in targets:
            samples = results.get(target, {}).get(scenario)
            if samples:
                result = summarize(samples)
                cells.append(f"{result.median_ns:.0f} ({result.stdev_ns:.0f})")
            else:
                cells.append("—")
        ratio = paired_ratio(
            results.get("laminite", {}).get(scenario, []),
            results.get("starlette", {}).get(scenario, []),
        )
        status = ""
        if scenario in failed:
            status = "FAIL"
        elif scenario in BASE_SCENARIOS and gated:
            status = "PASS"
        ratio_text = f"{ratio:.2f}" if ratio is not None else "—"
        lines.append("| " + " | ".join([scenario, *cells, ratio_text, status]) + " |")
    return "\n".join(lines)


def _failing_scenarios(failures: list[str]) -> set[str]:
    return {failure.split(":", 1)[0] for failure in failures}


def render_ab(
    results: dict[str, tuple[TargetResult, TargetResult, float]], *, failed: set[str]
) -> str:
    lines = ["| Scenario | Base ns | Head ns | Δ% | Status |", "|---|---:|---:|---:|---|"]
    for scenario, (base, head, delta) in results.items():
        status = "FAIL" if scenario in failed else "PASS"
        lines.append(
            f"| {scenario} | {base.median_ns:.0f} ({base.stdev_ns:.0f}) | "
            f"{head.median_ns:.0f} ({head.stdev_ns:.0f}) | {delta:+.1f}% | {status} |"
        )
    return "\n".join(lines)


def emit(markdown: str, summary_path: str | None) -> None:
    print(markdown)
    destination = summary_path or os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def _non_negative_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be a finite value >= 0")
    return number


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="benchmarks.run_micro",
        description="In-process microbenchmarks with interleaved rounds and CI gates.",
    )
    parser.add_argument("--quick", action="store_true", help="use fewer rounds and iterations")
    parser.add_argument(
        "--gate",
        action="store_true",
        help="fail when any of S1-S5 is not faster than Starlette",
    )
    parser.add_argument(
        "--compare-ref",
        metavar="REF",
        help="compare against a git ref (e.g. origin/main) and fail on regressions",
    )
    parser.add_argument(
        "--max-regression",
        type=_non_negative_float,
        default=DEFAULT_MAX_REGRESSION,
        help="allowed A/B regression in percent (default: %(default)s)",
    )
    parser.add_argument(
        "--rounds", type=_positive_int, default=None, help="rounds to measure (default: auto)"
    )
    parser.add_argument(
        "--iterations",
        type=_positive_int,
        default=None,
        help="requests per round (default: auto)",
    )
    parser.add_argument(
        "--summary",
        default=None,
        help="append the Markdown report to this file (default: $GITHUB_STEP_SUMMARY)",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--laminite-src", default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global _laminite_src
    args = parse_args(argv)
    if args.laminite_src is not None:
        _laminite_src = Path(args.laminite_src).resolve()
        sys.path.insert(0, str(_laminite_src))
    if args.worker:
        return run_worker(args.iterations or DEFAULT_ITERATIONS)

    rounds = args.rounds or (QUICK_ROUNDS if args.quick else DEFAULT_ROUNDS)
    iterations = args.iterations or (QUICK_ITERATIONS if args.quick else DEFAULT_ITERATIONS)
    failures: list[str] = []
    sections = [
        f"### Microbenchmarks\n"
        f"Rounds: {rounds}, iterations/round: {iterations}. "
        f"Values are median (stdev) ns per request; gates use the median of per-round ratios."
    ]

    if args.gate or not args.compare_ref:
        gate_runs: list[tuple[dict[str, dict[str, list[float]]], set[str], list[str]]] = []
        # Confirm a Starlette-gate failure once so one noisy run cannot block CI.
        for attempt in range(2):
            try:
                results = collect_framework_results(rounds=rounds, iterations=iterations)
            except Exception as exc:
                # Harness failures must exit 2, not look like a confirmed gate failure.
                sections.append(f"### Framework comparison\n\nComparison failed: {exc}")
                emit("\n\n".join(sections), args.summary)
                print(f"benchmark comparison failed: {exc}", file=sys.stderr)
                return 2
            gate_failures = _gate_failures(results) if args.gate else []
            gate_failed = _failing_scenarios(gate_failures)
            gate_runs.append((results, gate_failed, gate_failures))
            if not gate_failed:
                break
            if attempt == 0:
                print(
                    "Starlette gate failure; re-running once to confirm it is not runner noise.",
                    file=sys.stderr,
                )
        confirmed = gate_runs[0][1] & gate_runs[-1][1] if len(gate_runs) > 1 else gate_runs[0][1]
        if len(gate_runs) > 1:
            for index, (run_results, run_failed, _) in enumerate(gate_runs, 1):
                sections.append(f"#### Comparison run {index}")
                sections.append(render_comparison(run_results, failed=run_failed, gated=args.gate))
            sections.append("The gate fails only for scenarios that fail in both runs.")
        else:
            sections.append(render_comparison(gate_runs[0][0], failed=confirmed, gated=args.gate))
        failures.extend(
            failure for failure in gate_runs[-1][2] if _failing_scenarios([failure]) & confirmed
        )

    if args.compare_ref:
        ab_runs: list[
            tuple[dict[str, tuple[TargetResult, TargetResult, float]], set[str], list[str]]
        ] = []
        # A regression on a shared runner may be a transient spike; confirm it
        # once. Only scenarios that fail in both runs fail the gate.
        for attempt in range(2):
            try:
                ab_results, ab_failures = run_ab(
                    args.compare_ref,
                    rounds=rounds,
                    iterations=iterations,
                    max_regression=args.max_regression,
                )
            except (RuntimeError, OSError) as exc:
                sections.append(f"### A/B vs `{args.compare_ref}`\n\nA/B comparison failed: {exc}")
                emit("\n\n".join(sections), args.summary)
                print(f"A/B comparison failed: {exc}", file=sys.stderr)
                return 2
            failed_scenarios = _failing_scenarios(ab_failures)
            ab_runs.append((ab_results, failed_scenarios, ab_failures))
            if not failed_scenarios:
                break
            if attempt == 0:
                print(
                    "A/B regression detected; re-running once to confirm it is not runner noise.",
                    file=sys.stderr,
                )
        confirmed = ab_runs[0][1] & ab_runs[-1][1] if len(ab_runs) > 1 else ab_runs[0][1]
        if len(ab_runs) > 1:
            for index, (run_results, run_failed, _) in enumerate(ab_runs, 1):
                sections.append(f"#### A/B run {index} vs `{args.compare_ref}`")
                sections.append(render_ab(run_results, failed=run_failed))
            sections.append("The gate fails only for scenarios that regress in both runs.")
        else:
            sections.append(f"### A/B vs `{args.compare_ref}`")
            sections.append(render_ab(ab_runs[0][0], failed=confirmed))
        failures.extend(
            failure for failure in ab_runs[-1][2] if _failing_scenarios([failure]) & confirmed
        )

    emit("\n\n".join(sections), args.summary)

    if failures:
        print("Performance gate failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
