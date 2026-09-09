# AccessRide Recovery

A deliberately narrow, failure-first workflow for recovering an accessible ride disruption. It evaluates only approved providers and requires a human approval before any handoff. It is not a booking system.

## Local validation

```sh
PYTHONPATH=src python3 -m compileall -q src tests
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Fixture demo

`accessride.api.app` serves the Phase 4 fixture slice at `/demo`. It is intentionally
single-process and in-memory: restarting the process resets the demo incident and any
referral packet. It creates no booking, call, or dispatch action. A production deployment
needs transactional persistence and distributed locking before preserving these guarantees
across workers.

## CALL-E integration status

Phase 5 adds a fail-closed, injected transport boundary and local contract
tests for a future CALL-E verification adapter. It has no configured CLI,
credentials, or network path. Planning remains bound to an open,
roster-approved attempt; running remains impossible without a separate,
single-use explicit operator call authorization. Polling is bounded by both a
deadline and a poll limit. The fixture adapter remains the only demo behavior.

The local CALL-E documentation does not define the correlation and typed
capability-assertion schema this workflow needs. The first explicitly approved
live smoke test must map actual CALL-E plan/run/status JSON into the documented
strict envelope and verify that mapping before a live transport is enabled.
Transcripts and summaries can never create verified compatibility evidence.

## First live CALL-E smoke-test package

Phase 6 adds an offline-only preflight, placeholder configuration, exact
availability/accessibility call brief, typed evidence examples, runbook, and
operator approval checklist. It still cannot make a call. See
[the smoke-test package](docs/calle-first-live-smoke-test.md) and
[operator checklist](docs/calle-operator-approval-checklist.md). The preflight
is intentionally blocked unless a local one-shot approval flag and all safe
configuration checks are present; it never invokes the CALL-E CLI.

## Direction

The API is a small FastAPI seam. The future operator console uses server-rendered Jinja2 fragments, HTMX actions, and SSE for status updates; it must preserve the same approval and evidence policies as the domain layer.

See [docs/architecture.md](docs/architecture.md) and [docs/safety.md](docs/safety.md).
