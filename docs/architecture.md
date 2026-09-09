# Architecture

The domain layer performs deterministic compatibility assessment. An approved-provider roster is the eligibility boundary; `Provider` values themselves make no approval claim. The decision registry owns roster and evidence inputs and internally evaluates an incident/provider pair before storing its immutable decision, which contains incident and provider identifiers, requirement/evidence snapshots, evaluation time, status, and validity deadline. It has no registration path for caller-constructed decisions. A handoff service accepts only that exact registry-backed decision.

Human approval is a separately issued immutable record bound to one incident, provider, compatibility decision, referral action, operator, timestamp, and expiry. Its gate consumes it once. The incident state machine records every permitted transition with actor, reason, and timestamp; `HANDOFF` is reachable only through the validated service.

Core incidents have no free-text notes or mobility disclosure. Transport-facing content is generated only from controlled operational requirement keys.

Future UI: Jinja2-rendered pages, HTMX partial actions, and SSE read-only status updates. UI convenience must not bypass domain safety gates.
