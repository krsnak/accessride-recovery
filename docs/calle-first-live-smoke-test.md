# Phase 6: first live CALL-E smoke test

This is one operator-approved, controlled-recipient **availability and
accessibility verification**. It is not dispatch, a reservation, a booking,
payment collection, or a medical service. Consider only a provider already
approved by the roster. The package is competition-demo sized: the local
preflight is offline and does not invoke CALL-E.

## Exact call brief

> Hello. This is an AccessRide Recovery accessibility verification for a
> controlled test. This is only to check availability and accessibility; it is
> **not a reservation and please do not book, dispatch, hold, or charge
> anything**. May I confirm concrete operational facts: whether an appropriate
> vehicle is available; whether boarding uses a lift or ramp; whether an
> occupied wheelchair can be secured; and, if a power wheelchair is part of
> the controlled requirement list, whether it is compatible? Please also
> confirm pickup/service-area coverage and estimated arrival time. Only if it
> is necessary to assess the test, what authorization or payment route would
> apply? If any fact cannot be confirmed, please say it is unknown. We do not
> need any diagnosis or medical history. Thank you; no booking will be made.

The selected controlled `RequirementCode` values are the sole transport-facing
disclosure. Do not add free-text rider details. `power_wheelchair_compatibility`
is asked only when it appears in the immutable request snapshot.

## Expected extracted output and evidence mapping

The future approved caller must map only explicit, concrete answers into the
existing `CalleVerificationAssertion` / `CapabilityEvidence` model. Every item
needs the immutable run-derived evidence ID, roster-approved provider ID,
controlled `RequirementCode`, typed `AssertionState`, scalar value, provenance,
and timezone-aware `observed_at`/`expires_at`. The adapter accepts evidence only
from its registered completed run and only for the immutable request snapshot.

| Response | Typed assertion | Compatibility consequence |
| --- | --- | --- |
| “Yes, a lift-equipped vehicle is available.” | `vehicle_availability: VERIFIED / true`; `lift: VERIFIED / true` | Can support verification if every hard requirement is current and verified. |
| “We cannot secure an occupied wheelchair.” | `wheelchair_securement: INCOMPATIBLE / false` | `INCOMPATIBLE` for a hard securement requirement. |
| “I think we might have one; I cannot confirm.” | `vehicle_availability: UNKNOWN / null` | `UNKNOWN`; never upgraded from an ambiguous answer. |
| “We can probably handle it” (no boarding or securement fact), missing timestamp, unsupported key, or text such as ETA “soon” | Reject as malformed; emit no evidence for that claim | `UNKNOWN` remains, or the run is rejected by the typed adapter. |

Examples of valid scalar semantics: boolean capability keys use `VERIFIED/true`,
`INCOMPATIBLE/false`, or `UNKNOWN/null`; `eta` uses a numeric number of minutes
when verified; area/route keys use a concrete text value when verified. A
positive statement about one key cannot imply another key. Transcript and
summary free text never become evidence.

## Offline preflight

Copy no values into the repository. In an operator-controlled shell, load
placeholder-derived local configuration by name and run:

```sh
PYTHONPATH=src .venv/bin/python scripts/calle_smoke_preflight.py
```

It requires a clean repository, healthy local fixture, controlled-recipient
E.164 format, consent/control confirmation, operator identity, a literal
one-shot authorization state, approved objective/disclosure, requirement
allowlist, `max_calls=1`, and bounded duration/deadline/polls. Its diagnostics
name missing configuration only; they do not print values or secrets. It has no
CALL-E CLI, credential, subprocess-to-CALL-E, or network path.

`--operator-one-shot-approval` is a local assertion that the operator has
approved exactly one run. It does not execute anything and is not a substitute
for the existing single-use `CalleCallAuthorization` bound to the actual plan.
Without it, preflight reports that live execution remains disabled.

## Runbook (for a future separately authorized real call)

1. Run offline preflight and resolve every blocker.
2. Operator reviews the actual controlled recipient number and the exact brief above.
3. **Mandatory approval point:** operator explicitly approves one immutable,
   roster-approved attempt and one call authorization bound to its incident,
   provider, attempt, and plan. Record the approval before proceeding.
4. Create exactly one CALL-E plan. Confirm its returned identifiers and map it
   into the strict local plan envelope; do not run if mappings differ.
5. Use the one authorization for exactly one run.
6. Poll status only with the returned run ID, at the configured bounded cadence,
   stopping at terminal status, deadline, poll limit, refusal, any booking
   prompt, or any missing/ambiguous safety fact.
7. Extract only typed assertions, then use the registered adapter to issue
   evidence and evaluate compatibility.
8. Make no booking, handoff, payment, reservation, dispatch, or follow-up call.
9. Capture a redacted audit record: configuration names, approval ID, plan/run
   IDs, status sequence, assertion IDs/states, compatibility result, stop reason,
   and explicit no-booking confirmation.

## Local CALL-E CLI facts and unresolved mapping

Read-only `calle --help` locally confirmed command names `call plan`, `call
run`, and `call status`; the installed local documentation says planning accepts
`--to-phone`, `--goal`, optional language/region/timezone; run takes returned
`--plan-id` and `--confirm-token`; status takes returned `--run-id`. It places
run status under `status_result.structuredContent` (after run) or
`result.structuredContent` (status query). No plan/run/status command was
executed for this package.

Unresolved before the first real call: prove the actual JSON can carry or be
bound to `incident_id`, `provider_id`, `provider_attempt_id`, idempotency key,
immutable requirement/disclosure snapshot, timezone-aware timestamps,
provenance, and typed assertion array. The CLI documentation does not establish
those fields. If it cannot echo them, implement and review a signed local
binding before enabling `LiveCalleAdapter`; do not infer them from transcript
text.
