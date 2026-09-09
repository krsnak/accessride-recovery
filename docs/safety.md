# Safety model

- Only a provider resolved by the approved-provider roster is a handoff candidate; a caller-created `Provider` is never proof of approval.
- Evidence is an immutable typed assertion (`VERIFIED`, `UNKNOWN`, or `INCOMPATIBLE`) with identifier, field key, provider, provenance, observed time, and expiry. Timestamps are timezone-aware and observations cannot be treated as current before they occur.
- A decision can be `VERIFIED_COMPATIBLE` only when at least one hard operational requirement exists and every hard requirement has current `VERIFIED` evidence with no current `UNKNOWN` or `INCOMPATIBLE` assertion. Missing, future-only, or explicit-unknown hard evidence is `UNKNOWN`; only past stale matching evidence is `EXPIRED`. Soft preferences never block an otherwise hard-compatible provider and never affect a verified decision's validity deadline.
- A human approval record is mandatory before handoff and binds exactly one incident, provider, stored compatibility decision, referral action, operator, and expiry; it is consumed once.
- `HANDOFF` is an authoritative state transition only after roster, decision, and approval validation. Caller-supplied status enums and direct incident transitions cannot authorize it.
- Handoff is a referral record, never a booking. CALL-E fixture code prepares an operator brief only; live calls need explicit operator approval and a future, separately reviewed adapter.

Transport-facing disclosure is generated from whitelisted operational requirement keys only: vehicle availability; wheelchair or power-wheelchair compatibility; boarding, lift, ramp, and securement; dimensions and weight capacity; service animal; pickup/service area; ETA or arrival deadline; and authorization/payment route. Do not add notes, diagnoses, or arbitrary text to the core domain.
