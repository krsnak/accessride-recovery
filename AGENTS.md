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

## Autonomous workflow

- Work autonomously inside the currently approved project scope. Do not stop for routine confirmations between implementation, tests, review, fixes, documentation, commit, and push.
- The default delivery loop is: inspect current state -> implement -> run the relevant full validation suite -> perform adversarial/self review -> fix all blockers -> re-run validation -> commit -> push to the current project branch.
- If review finds ordinary code defects, test gaps, edge cases, dependency issues, or documentation inconsistencies, fix them and continue without asking the operator.
- Safe refactors, test additions, fixture changes, UI improvements, dependency/environment setup inside a project-local virtual environment, and documentation updates do not require separate operator confirmation when they preserve the approved architecture and safety boundaries.
- After a successful validated phase, commit and push automatically unless the operator explicitly asked for an uncommitted review state.
- Prefer completing a coherent phase and reporting the final result instead of repeatedly asking whether to continue.

### Escalate to the operator only when

- a real CALL-E phone call would be planned or executed;
- any booking, payment, reservation, message, deployment, PR submission, Devpost submission, or other externally consequential action would occur unless that exact action was already explicitly authorized;
- a proposed change materially alters product scope, core architecture, safety policy, privacy boundary, or the approved-provider/human-approval model;
- credentials, secrets, account permissions, paid services, or irreversible/destructive actions are required;
- a blocker cannot be resolved safely from repository state, tests, documentation, or available tools;
- two materially different product/architecture choices remain and the choice affects judging strategy or user experience.

- Routine progress/status updates are not approval gates. Continue working after them unless an escalation condition above is reached.
- The standing prohibition remains absolute: never perform a live CALL-E call or autonomous booking without explicit operator approval for that action.

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
