# AccessRide Recovery contributor guide

## Purpose

AccessRide Recovery is a failure-first recovery workflow for a rider whose accessible ride has failed or is at risk of failing. It helps an operator assess approved providers, compatibility, evidence, and a human-approved handoff.

## Safety and scope

- This is not a marketplace, generic dispatch system, autonomous booking product, emergency service, or medical service.
- Only explicitly approved providers may be considered or handed off to.
- Collect and disclose only the operational mobility requirements needed to assess compatibility; never add diagnoses or unnecessary personal details.
- Compatibility statuses are deterministic and evidence-backed: `VERIFIED_COMPATIBLE`, `UNKNOWN`, `INCOMPATIBLE`, and `EXPIRED`.
- `UNKNOWN` must never become verified without current supporting evidence.
- Provider approval comes only from an approved-provider roster/service; never treat a `Provider` field or caller input as authority.
- A handoff requires the exact registry-backed immutable compatibility decision and a single-use, unexpired approval bound to its incident, provider, decision, action, and operator. Never accept a caller-supplied compatibility enum as authorization.
- `HANDOFF` must be reachable only through the validated handoff service. Preserve state-transition events and do not add direct mutation/bypass helpers.
- Evidence assertions need immutable IDs, provider/key, typed assertion state, provenance, and valid timezone-aware observation/expiry timestamps. Hard constraints are conservative; soft preferences cannot block hard-compatible providers.
- Core domain models must not carry free-text mobility notes or disclosures. Generate transport-facing disclosure only from controlled operational requirement keys.
- A human must explicitly approve every handoff. No code path may book a ride autonomously.
- A live CALL-E call requires explicit operator approval. The repository ships fixture-only CALL-E behavior; do not replace it with live behavior casually.
- CALL-E plans require an identity-registered authority issued by `RecoveryOrchestrator` for an open, roster-approved attempt and an exact immutable request/disclosure snapshot. CALL-E evidence can be issued only by the adapter for its registered run; matching IDs or provenance strings are not authority.
- Any future live CALL-E execution requires its own single-use, expiring operator call authorization bound to the incident, provider, attempt, and plan. It is separate from handoff approval.

## Coding conventions

- Python 3.12+, `src/` layout, type annotations, dataclasses, and standard library domain logic first.
- Keep integrations behind interfaces and keep policy decisions in `domain/` or `services/`, not API handlers.
- Prefer small deterministic functions and test safety boundaries directly.
- Never log or store more mobility information than the operational need.

## Repository structure

- `src/accessride/domain`: policy, models, and state transitions.
- `src/accessride/services`: orchestration interfaces and safety gates.
- `src/accessride/integrations`: adapter interfaces and fixture implementations.
- `src/accessride/persistence`: storage seams only.
- `src/accessride/api`, `src/accessride/web`: transport and presentation seams.
- `fixtures`, `docs`, `tests`: demos, architecture/safety notes, and checks.

## Required validation

Run before review (without network access):

```sh
PYTHONPATH=src python3 -m compileall -q src tests
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
