# Benchmarks

## Microbenchmarks (in-process)

Call each framework's ASGI `__call__` directly with a prebuilt `scope`. No HTTP server is involved.

```bash
uv sync --group bench
uv run --group bench python -m benchmarks.run_micro                          # compare frameworks
uv run --group bench python -m benchmarks.run_micro --quick                  # fewer rounds (CI-friendly)
uv run --group bench python -m benchmarks.run_micro --gate                   # fail if S1-S5 lose to Starlette
uv run --group bench python -m benchmarks.run_micro --compare-ref origin/main  # A/B regression gate vs a git ref
```

Every round measures each target once per scenario, rotating the target order so clock
drift and runner noise hit all targets equally. Reported numbers are the median of the
per-round means, and gates compare the median of the per-round ratios.
Laminite and Starlette are required; FastAPI and Litestar are optional (shown as `—`
when unavailable).

Scenarios:

| ID | Description |
|---|---|
| S1 | `GET /`, no params, JSON dict |
| S2 | `GET /items/{item_id}`, int path param |
| S3 | `GET /search?q=foo&limit=10`, two query params |
| S4 | `POST /items`, ~200 byte JSON body |
| S5 | `GET /missing`, 404 |
| S6 | `GET /di`, request-scoped DI provider |
| S7 | `GET /secure`, typed header + cookie + `msgspec.Meta` validation |
| S8 | `GET /files/a/b/c.txt`, `{file_path:path}` wildcard |

Gates:

- `--gate`: S1–S5 must all beat Starlette (`laminite / starlette < 1.0`), otherwise
  the comparison is re-measured once and only scenarios that keep losing exit the
  process with code 1.
- `--compare-ref REF`: A/B comparison against `REF` interleaved in the same run using
  two worker processes (head and a `git worktree` of `REF`, with a venv synced from
  the ref's own lockfile). Any scenario whose regression exceeds `--max-regression`
  (default +5%) is re-measured once; only scenarios that regress in both runs fail
  the gate with exit code 1. If the ref predates one of the feature scenarios, that
  scenario is skipped with a warning and the remaining scenarios are still compared.
- Both gates append a Markdown report to `$GITHUB_STEP_SUMMARY` (or `--summary PATH`).

## E2E (Granian + oha)

```bash
# Terminal 1
uv sync --group bench
uv run granian --interface asgi --host 127.0.0.1 --port 8000 benchmarks.e2e:app

# Terminal 2
oha -n 50000 -c 100 http://127.0.0.1:8000/
```

Record CPU, OS, Python, and library versions with the results below.

## Results

Quick in-process pass (`uv run python -m benchmarks.run_micro --quick`), median ns/request
(lower is better):

| Scenario | Laminite | Starlette | FastAPI | Litestar |
|---|---:|---:|---:|---:|
| S1 | 1669 | 5830 | 20817 | 10783 |
| S2 | 3683 | 6434 | 29725 | 12470 |
| S3 | 4545 | 9304 | 38866 | 12924 |
| S4 | 4193 | 10285 | 35002 | 14547 |
| S5 | 1679 | 8244 | 15740 | 15950 |
| S6 | 2321 | 6945 | — | — |
| S7 | 3512 | 8817 | — | — |
| S8 | 3648 | 7662 | — | — |

FastAPI and Litestar builders cover S1–S5; S6–S8 are measured for Laminite and Starlette only.

Environment for the table above:

- Machine: developer laptop (Apple Silicon)
- OS: macOS (darwin)
- Python: 3.12
- Notes: single-process quick mode; treat as indicative. On a quiet machine, raise
  `--rounds` / `--iterations` for publication numbers.

CI runs `benchmarks.run_micro --quick --gate` and, on pull requests, also
`--compare-ref origin/main`; any gate failure fails the workflow.
