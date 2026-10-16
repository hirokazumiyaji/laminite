# Sync handlers and `offload`

Synchronous handlers run directly on the event loop. Blocking work will stall other requests.

Use `offload=True` to run a sync handler in `asyncio.to_thread`:

```python
@app.get("/report", offload=True)
def report():
    return generate_report()  # blocking
```

`offload=True` on an async handler raises `TypeError` at registration/freeze time.
