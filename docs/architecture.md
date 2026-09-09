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
## Phase 5 live CALL-E transport boundary

`LiveCalleAdapter` is production-shaped plumbing, not a configured live
integration. It accepts only an explicitly injected `CalleTransport`; without
one (or without the concrete orchestrator) it remains unavailable and performs
no CALL-E action. It never reads credentials, constructs a CLI command, or
contacts CALL-E itself. `InMemoryCalleTransport` provides the local contract
test seam.

The local CALL-E CLI documentation establishes `plan_id`, `confirm_token`,
`run_id`, and status locations, but does not establish a stable response schema
for AccessRide's incident/provider/attempt/idempotency correlation or typed
capability assertions. Accordingly the adapter currently requires every plan
and run/status transport envelope to contain exact `incident_id`,
`provider_id`, `provider_attempt_id`, `idempotency_key`, and plan/run IDs; a
plan must also echo the exact controlled requirements, disclosure, and request
timestamp. It accepts only documented terminal statuses plus `PLANNED`,
`IN_PROGRESS`, and `RINGING`; unknown values, missing fields, duplicate IDs,
wrong provider/snapshot, malformed assertions, status identity changes,
out-of-order updates, and terminal regression are rejected.

Run execution still consumes the separate, single-use, expiring,
operator-issued `CalleCallAuthorization` immediately before the transport
boundary. A cached adapter-issued run is the only replay path; retries before a
registered run require a newly issued authorization. No transcript, summary,
or other free text is parsed as evidence. Only controlled typed assertions from
an adapter-issued completed run can become evidence.

`monitor_call_run` has a required timezone-aware deadline and positive maximum
poll count, and stops on deadline or terminal status. Scheduling/delay is left
to the future approved caller, so this local boundary contains no sleeping or
unbounded loop.

The first explicitly approved live smoke test must provide the minimal
transport mapping from actual CLI JSON into that envelope, confirm whether
CALL-E can echo the required correlations (or whether a signed local binding
is needed), validate the assertion source/schema, then perform one authorized
plan, one separately authorized run, and bounded status reads. Until then no
live transport should be wired in production.

`fixtures/calle_provider_verification.json` demonstrates three approved
candidates: Provider A lacks required lift/boarding capability and is
incompatible; Provider B has a vehicle but cannot verify securement and stays
unknown; Provider C verifies every hard mobility constraint. C also verifies
ETA, pickup/service area, and authorization/payment-route facts, but these
soft operational facts do not substitute for hard safety constraints.
