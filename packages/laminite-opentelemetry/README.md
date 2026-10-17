# laminite-opentelemetry

OpenTelemetry instrumentation for [Laminite](https://github.com/hirokazumiyaji/laminite).

```python
from laminite import Laminite
from laminite_opentelemetry import instrument_app

app = Laminite()
instrument_app(app)
```

Requires Laminite's `laminite.route` scope key and `on_unhandled_exception` hook (#29).
