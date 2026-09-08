# Agent log

A record of engineering judgment on this assignment. Each entry names what the
agent produced, what was decided, and the authority for the decision. Preference
is not recorded here.

## Accepted: V-6 is not a policy-field rule

**Produced.** The rule implementation listed `POLICY_RULES` as pure functions of
`(notification, policy)` and kept `evaluate_duplicate_notification` out of that
table. Duplicate matching goes through `repository.find_matching`.

**Decision.** Keep it. `evaluate_notification` takes a notification and a policy
and does not touch the store. V-6 runs in `submit_notification`, after V-7 and
before V-2.

**Reason.** WI-0151 AC-1 defines a duplicate as three fields matching an
*existing recorded* notification. AC-3 is the converse: a refusal was never
written, so a later submission is not a duplicate. Section 4.1 places V-6
between V-7 and V-2; that is a store query sitting among coverage comparisons,
not a field on `Policy`. Putting `find_matching` inside
`evaluate_notification(notification, policy)` is impossible without giving that
function the repository. Putting V-6 in `POLICY_RULES` without a store would
accept a second notice of the same loss, which is the defect WI-0151 exists to
stop.

## Corrected: evaluation must not call the policy client

**Produced.** The first implementation of `evaluate_notification` took
`policy_client` and `repository`, looked up the policy itself, and returned a
`ValidationOutcome`. `PolicyNotFound` was caught next to that lookup.
`PolicyLookupFailed` was left uncaught in the same function.

**Decision.** Reject that signature. `evaluate_notification` takes a
notification and a policy and returns `RuleFailure | None`.
`submit_notification` resolves the policy. `PolicyNotFound` becomes V-1 there.
`PolicyLookupFailed` is not caught.

**Reason.** Contract section 1: a policy the master cannot read is specified
separately from a policy that does not exist, because the caller must act
differently. Section 5: `rule` is present only when a section 4 rule decided the
refusal; `POLICY_LOOKUP_FAILED` has no `rule`, and its `detail` is `reason`, not
a comparison. Section 6 maps that `reason` to 504 / 503 / 502, and maps
`POLICY_NOT_FOUND` to 422. A function that already calls `get_policy` is where
the next change catches `PolicyLookupFailed` and returns a `RuleFailure`. That
would send a timeout to the handler as if their policy number were wrong
(WI-0142 AC-4 is not this case). Do not catch what you cannot answer.

## Gate observation, then fix

**Observed.** PR https://github.com/yashvcdeshmukh/claims-intake/pull/2. A
commit that failed pytest (`be9c519`) made the `checks` job red. Ruff and mypy
passed. GitHub reported `mergeable: true` and `mergeable_state: unstable`. Merge
was still allowed.

**Authority for treating that as a defect.** A failing check that does not
block merge is not a gate. The workflow ran; the repository did not require it.
`GET /repos/yashvcdeshmukh/claims-intake/branches/main/protection` returned 404
(`Branch not protected`). Rulesets were empty.

**Fix.** Required the `checks` job on `main` (`strict: true`, `enforce_admins:
true`). The same PR then reported `mergeable_state: blocked`. Merge is no longer
allowed while pytest is red.
