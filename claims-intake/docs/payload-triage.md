# Payload Triage

Every payload in `data/fnol_edge.json` classified against `docs/api-contract.md` as you have completed it. The classification records what the contract says the service does, which is not always what the payload obviously violates.

Fill one row per payload. Where a payload is accepted, leave the rule, code, and status columns as `-`.

## Classification

| Payload | Outcome | Rule | Code | Status |
| --- | --- | --- | --- | --- |
| EDGE-01 | accepted | - | - | - |
| EDGE-02 | accepted | - | - | - |
| EDGE-03 | accepted | - | - | - |
| EDGE-04 | rejected | V-7 | POLICY_CANCELLED | 422 |
| EDGE-05 | rejected | V-2 | LOSS_BEFORE_INCEPTION | 422 |
| EDGE-06 | rejected | V-4 | AMOUNT_EXCEEDS_LIMIT | 422 |
| EDGE-07 | rejected | V-1 | POLICY_NOT_FOUND | 422 |
| EDGE-08 | rejected | - | MALFORMED_REQUEST | 400 |
| EDGE-09 | rejected | V-5 | TYPE_NOT_COVERED | 422 |
| EDGE-10 | rejected | V-7 | POLICY_CANCELLED | 422 |
| EDGE-11 | rejected | - | MALFORMED_REQUEST | 400 |
| EDGE-12 | rejected | - | MALFORMED_REQUEST | 400 |

## Decision log

Three payloads cannot be classified against the contract as it shipped, because the contract left a decision unmade. For each one, record the ambiguity, the decision, its authority, and the alternative you rejected.

A decision recorded here and nowhere else has not been made. Amend `docs/api-contract.md` so that a reader of the contract alone could not arrive at the other reading.

### Decision 1

**Payload.** EDGE-07 (`mot-4471`).

**The ambiguity.** Exact match vs case-insensitive lookup.

**Decision.** Exact, including case. `POLICY_NOT_FOUND`.

**Authority.** Section 2.2: identifier as held in the master. V-1 / WI-0142 AC-4.

**Rejected alternative.** Fold case and accept. That records a number the caller did not send.

**Contract amended.** Section 4.2: `policy_number` matched exactly, including case.

### Decision 2

**Payload.** EDGE-11 (`claim_type`: `flood`).

**The ambiguity.** 400 vs V-5 `TYPE_NOT_COVERED`.

**Decision.** `MALFORMED_REQUEST` / 400. V-5 does not run.

**Authority.** Section 2.3: vocabulary is fixed; V-5 is the product subset of that vocabulary.

**Rejected alternative.** `TYPE_NOT_COVERED`. That code is for a defined peril this product excludes (EDGE-09), not a type the API does not define.

**Contract amended.** Section 4.2: a `claim_type` not in 2.3 is `MALFORMED_REQUEST`.

### Decision 3

**Payload.** EDGE-12 (`estimated_amount`: `3499.999`).

**The ambiguity.** 400 vs coerce/round and run V-4 (which would pass).

**Decision.** `MALFORMED_REQUEST` / 400. No rounding.

**Authority.** Section 2.2: two decimal places.

**Rejected alternative.** Accept 3499.999 or 3500.00. That records an amount the caller did not send in the form this contract defines.

**Contract amended.** Section 4.2: not exactly two decimal places is `MALFORMED_REQUEST`, not V-4.

## Reconciliation (Day 2)

Checked every `NotificationRequest` and `ClaimRecord` refusal against section 6.

Model refusals: extra field, missing required field, empty `policy_number`, `claim_type` outside 2.3, empty `claim_type`, `loss_date` not a calendar date, `estimated_amount` not exactly two decimal places, not greater than zero, or a float. All of these are `MALFORMED_REQUEST` / 400 (`detail.issue` distinguishes them). Section 6 already has that row.

`not_json` never reaches the model (the HTTP layer fails first). Policy construction errors are not HTTP responses.

Nothing was added to section 6. The codes the models produce are already listed.
