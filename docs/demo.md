# Demo outline

1. Load `fixtures/demo_incident.json`.
2. Load `fixtures/calle_provider_verification.json` through the fixture-only
   CALL-E lifecycle using an orchestrator-issued open-attempt planning authority
   (`plan_call`, `run_call`, `get_call_run`). No call is made.
3. Ask the fixture adapter to issue evidence for its exactly correlated,
   registered run and evaluate current capability evidence
   and evaluate deterministic compatibility: A is incompatible, B is unknown,
   and C is verified compatible.
4. Move the incident to human approval, approve it, then record the handoff.
5. Show the fixture CALL-E operator brief; no booking or call is made.
