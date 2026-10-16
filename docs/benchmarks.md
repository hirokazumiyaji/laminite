# Benchmarks

## Microbenchmarks (in-process)

Call each framework's ASGI `__call__` directly with a prebuilt `scope`. No HTTP server is involved.

```bash
uv sync --group bench
uv run python -m benchmarks.run_micro
```

Scenarios:

| ID | Description |
|---|---|
| S1 | `GET /`, no params, JSON dict |
| S2 | `GET /items/{item_id}`, int path param |
| S3 | `GET /search?q=foo&limit=10`, two query params |
| S4 | `POST /items`, ~200 byte JSON body |
| S5 | `GET /missing`, 404 |

Acceptance for v0.1.0: Laminite mean time per request is lower than Starlette on S1–S5.

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

Quick in-process pass (`uv run python -m benchmarks.run_micro --quick`), mean ns/request (lower is better):

| Scenario | Laminite | Starlette | FastAPI | Litestar |
|---|---:|---:|---:|---:|
| S1 | 34580 | 41018 | 60734 | 46439 |
| S2 | 36534 | 40067 | 69967 | 47554 |
| S3 | 38090 | 43388 | 79222 | 49129 |
| S4 | 35422 | 44905 | 77652 | 56683 |
| S5 | 32581 | 40003 | 49042 | 54539 |

Environment for the table above:

- Machine: developer laptop (Apple Silicon)
- OS: macOS (darwin)
- Python: 3.12
- Notes: single-process quick mode; treat as indicative. Prefer a dedicated host + full `pyperf` run for publication numbers.

CI runs `benchmarks.run_micro --quick` and posts results to the job summary without failing the workflow.
