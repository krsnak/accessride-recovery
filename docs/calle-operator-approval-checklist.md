# First live CALL-E operator approval checklist

Do not authorize the call unless every item is affirmative.

- Recipient: I reviewed the exact E.164 number and confirm it is a controlled test recipient.
- Control/consent: I record why the recipient controls or consents to use of this number.
- Provider: the provider is currently approved by the roster and the attempt is open.
- Brief: I reviewed the exact brief in `docs/calle-first-live-smoke-test.md`; it is availability/accessibility verification only.
- Disclosure: only the immutable controlled requirement keys will be disclosed; no diagnosis or medical history.
- Limit: `max_calls = 1`; maximum call duration is 180 seconds and the total deadline is 240 seconds.
- Output: expected fields are `requirement_code`, `assertion`, scalar `value`, `observed_at`, `expires_at`, provider/provenance, and immutable plan/run correlations.
- Stop: stop for no answer, refusal, deadline/poll limit, any booking/payment request, malformed response, or any ambiguous hard requirement.
- No booking: I explicitly authorize no booking, reservation, dispatch, payment, handoff, or follow-up call.
- Approval: I issue one single-use, expiring call authorization bound to this incident, provider, attempt, and plan immediately before the run.
