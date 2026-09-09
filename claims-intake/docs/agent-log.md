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

## Accepted: parse is explicit so FastAPI 422 cannot leak

**Produced.** `POST /notifications` reads the raw body, calls `json.loads`, then
`NotificationRequest.model_validate`. Parse failures become `MALFORMED_REQUEST`
with status 400. The endpoint body is not typed as `NotificationRequest`.

**Decision.** Keep the explicit parse. Do not let FastAPI validate the body.

**Reason.** Section 6 is exhaustive: every refusal this service produces appears
in that table. FastAPI's default for a body it cannot bind is 422 with a
`detail` array. That status, for this condition, is not in section 6, and that
shape is not the section 5 envelope. Section 2.4 already assigned 400 /
`MALFORMED_REQUEST` to a request that cannot be interpreted. Typing the
parameter as `NotificationRequest` would make the framework emit a response the
contract does not describe.

## Accepted: RuleFailure stays two fields; the HTTP layer fills detail

**Produced.** Routes map `RuleFailure` to the section 5 envelope. Promised
`detail` keys are assembled from the parsed notification plus a follow-up read
of the policy or the store. `PolicyLookupFailed` is caught here and mapped by
`reason` to 504 / 503 / 502.

**Decision.** Do not add `detail` to `RuleFailure`. Do not catch
`PolicyLookupFailed` in `submit_notification`.

**Reason.** Day 2 locked `RuleFailure` at `{rule, code}`: those are the two
values a rule names. Section 5's `detail` is a property of the HTTP envelope,
not of the rule outcome. A second `get_policy` or `find_matching` after a
failure is a read to fill promised keys, not a re-evaluation. Catching
`PolicyLookupFailed` in the service and returning a `RuleFailure` would put a
`rule` on a refusal that had none, and would send a timeout to the handler as
`POLICY_NOT_FOUND` (the defect the Day 3 log already refused).

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
