# Demo outline

1. Load `fixtures/demo_incident.json`.
2. Load `fixtures/calle_provider_verification.json` through the fixture-only
   CALL-E lifecycle using an orchestrator-issued open-attempt planning authority
   (`plan_call`, `run_call`, `get_call_run`). No call is made.
3. Rebase the recorded fixture chronology to the in-memory service startup,
   then ask the fixture adapter to issue evidence for its exactly correlated,
   registered run and evaluate current capability evidence
   and evaluate deterministic compatibility: A is incompatible, B is unknown,
   and C is verified compatible.
4. Move the incident to options-ready, then use the sole human approval control for
   Provider C's registry-backed current decision. The rendered form carries a
   short-lived, single-use, process-local confirmation capability and rejects a
   hostile `Origin` when one is present. This is a local-demo UI safeguard, not
   production authentication or authorization. This records a referral handoff packet.
5. No booking or call is made. The demo is in-memory and resets when its single process restarts.
