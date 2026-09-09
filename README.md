# AccessRide Recovery

A deliberately narrow, failure-first workflow for recovering an accessible ride disruption. It evaluates only approved providers and requires a human approval before any handoff. It is not a booking system.

## Local validation

```sh
PYTHONPATH=src python3 -m compileall -q src tests
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Direction

The API is a small FastAPI seam. The future operator console uses server-rendered Jinja2 fragments, HTMX actions, and SSE for status updates; it must preserve the same approval and evidence policies as the domain layer.

See [docs/architecture.md](docs/architecture.md) and [docs/safety.md](docs/safety.md).
