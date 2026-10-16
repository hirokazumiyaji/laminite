# Testing

```python
from laminite.testing import TestClient

with TestClient(app) as client:
    res = client.get("/items/1", query={"q": "x"})
    assert res.status_code == 200
    assert res.json() == {"item_id": 1, "q": "x"}
```

`TestClient` uses a single `asyncio.Runner` for its lifetime so lifespan resources stay on one event loop. It has no httpx (or other HTTP client) dependency.

Without a `with` block, lifespan is skipped and the app freezes on the first request.
