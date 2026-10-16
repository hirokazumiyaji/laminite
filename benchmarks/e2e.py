"""Minimal app for Granian + oha E2E measurements."""

from laminite import Laminite

app = Laminite()


@app.get("/")
def index() -> dict[str, str]:
    return {"message": "ok"}
