# Architecture

The domain layer performs deterministic compatibility assessment. An approved-provider roster is the eligibility boundary; `Provider` values themselves make no approval claim. The decision registry owns roster and evidence inputs and internally evaluates an incident/provider pair before storing its immutable decision, which contains incident and provider identifiers, requirement/evidence snapshots, evaluation time, status, and validity deadline. It has no registration path for caller-constructed decisions. A handoff service accepts only that exact registry-backed decision.

Human approval is a separately issued immutable record bound to one incident, provider, compatibility decision, referral action, operator, timestamp, and expiry. Its gate consumes it once. The incident state machine records every permitted transition with actor, reason, and timestamp; `HANDOFF` is reachable only through the validated service.

Core incidents have no free-text notes or mobility disclosure. Transport-facing content is generated only from controlled operational requirement keys.

Future UI: Jinja2-rendered pages, HTMX partial actions, and SSE read-only status updates. UI convenience must not bypass domain safety gates.

## Provider-attempt orchestration

The in-memory orchestrator records operator-mediated provider attempts; it does
not place calls or book transport. Its `RLock` makes attempt accounting and
incident transitions atomic only within one process. This is sufficient for the
single-process MVP fixture/demo. A multi-worker or serverless deployment must
use transactional persistence plus transactional/distributed locking before
relying on the same single-flight, idempotency, cooldown, or call-ceiling
guarantees.

`DEADLINE` is valid only at or after the configured deadline, and
`CALL_CEILING` only after the configured ceiling is reached. They are never
relabelled as provider exhaustion. `PROVIDERS_EXHAUSTED` is intentionally
refused in the current policy: retries have cooldown but no finite
per-provider-attempt limit, so a nonempty approved roster cannot truthfully be
declared exhausted. In particular, an open attempt or a cooldown that expires
before deadline never establishes exhaustion. A future finite, policy-backed
provider-attempt rule may enable that stop only when no approved provider can
produce another attempt before deadline without violating single-flight,
cooldown, or the call ceiling; call-ceiling and deadline blockers must still
use their own conditions.

Every orchestration action is time-monotonic against both its own activity log
and the incident history: it cannot predate the incident's `reported_at` or its
latest transition event.

## Fixture CALL-E verification boundary

`integrations.calle` defines a typed `plan_call`, `run_call`, and `get_call_run`
contract for future verification calls. Planning requires a narrow capability
issued by `RecoveryOrchestrator` for an open, roster-approved attempt; the
request must exactly match that capability's incident, provider, attempt,
idempotency, requirement, disclosure, and request-time snapshot. The repository supplies only
`FixtureCalleAdapter`, which reads local, deterministic fixture results and
never invokes CALL-E, a phone, a network service, booking, or handoff. Its
output is correlated to the incident, approved-provider candidate, provider
attempt, and idempotency key. A result can become `CapabilityEvidence` only
through that adapter for its identity-registered run: caller-created runs,
requests, matching strings, and provenance cannot mint evidence.

Plan capabilities are revalidated against the orchestrator's current attempt
record and incident state for every use. Completing or failing the attempt, or
moving the incident to a terminal state (including cancellation or human
approval), makes the capability non-actionable.

The fixture adapter exposes controlled requirement-code disclosure only. Each
assertion has a controlled key, typed state/value semantics, immutable derived
evidence ID, provider, provenance, and timezone-aware observation/expiry times.
Assertions must be request-key subsets and must be observed no earlier than
the actual run start and no later than run completion. Duplicate run IDs, plan IDs, correlations, or
assertions for a key fail closed. `UNKNOWN` remains an evidence assertion, not
a positive result. The fixture lifecycle is protected by a process-local lock.
The `LiveCalleAdapter` placeholder implements no behavior and consumes its
separate single-use, expiring, operator-issued call authorization at the
future external execution boundary before returning its fail-closed unavailable
response; retries require fresh authorization, and a handoff approval never
substitutes for this authorization.

`fixtures/calle_provider_verification.json` demonstrates three approved
candidates: Provider A lacks required lift/boarding capability and is
incompatible; Provider B has a vehicle but cannot verify securement and stays
unknown; Provider C verifies every hard mobility constraint. C also verifies
ETA, pickup/service area, and authorization/payment-route facts, but these
soft operational facts do not substitute for hard safety constraints.
