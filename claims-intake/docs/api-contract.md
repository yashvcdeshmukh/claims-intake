# Claims Intake Service: API Contract

Version 0.4. Owned by the claims intake team. Consumed by the claims portal team.

This document is the authority on what the service accepts, what it returns, and under what conditions it refuses. Where the code and this document disagree, the document is correct and the code is a defect.

Sections 1 through 3 are fixed. Do not edit them.

## 1. Purpose and scope

The claims intake service accepts a first notice of loss from the claims portal, validates it against the policy master and a table of business rules, and either records a notification and issues a claim reference or refuses the submission with a specific reason.

**In scope.** Accepting a notification, validating it, and recording it. Issuing a claim reference. Reporting the reason a notification was refused.

**Out of scope.** Adjusting, reserving, payment, and any decision about coverage beyond the rules in section 4. The service decides whether a notification is well formed and admissible. It does not decide whether the claim will be paid.

**The policy master is a dependency, not part of this service.** The service reads policy records from it and does not write to it. A policy that cannot be read is a condition this contract specifies, and it is specified separately from a policy that does not exist, because the two require different action from the caller.

**Compatibility.** Adding a field to a response is a compatible change and callers must ignore fields they do not recognize. Adding a new error code is a compatible change and callers must fall through to default handling for a code they do not recognize. Changing the meaning of an existing code, removing a field, or changing a status code for an existing condition is not compatible and does not happen without a version increment agreed with the portal team.

## 2. Request



### 2.1 Endpoint

```
POST /notifications
Content-Type: application/json
```



### 2.2 Body


| Field              | Type    | Required | Notes                                                         |
| ------------------ | ------- | -------- | ------------------------------------------------------------- |
| `policy_number`    | string  | yes      | Identifier as held in the policy master. Not empty.           |
| `loss_date`        | string  | yes      | Calendar date, `YYYY-MM-DD`.                                  |
| `claim_type`       | string  | yes      | One of the values in 2.3. Not empty.                          |
| `estimated_amount` | decimal | yes      | United States dollars, two decimal places. Greater than zero. |
| `description`      | string  | no       | Free text. Absent and `null` are equivalent.                  |


The service rejects a body carrying a field not listed above. A misspelled field name is a defect in the caller's code, and accepting the payload with the field ignored would record a notification built from data the caller did not send.

### 2.3 Claim type vocabulary

`collision`, `theft`, `glass`, `liability`, `weather`.

Which of these are admissible on a given notification depends on the product the policy is written on. The vocabulary is fixed by this contract. The permitted subset is a property of the policy record and is evaluated by rule `V-5`.

### 2.4 Well formed against acceptable

A request that cannot be interpreted is refused with status `400`. This means the body was not valid JSON, a required field was absent, a field carried a value of the wrong type, or a field was present that this contract does not define. The caller's code is wrong.

A request that was interpreted and whose content is not admissible is refused with status `422`. The caller's data is wrong, and a person needs to see the reason.

This split is stated here once and holds without exception everywhere else in this document.

## 3. Success response

A notification that passes every rule in section 4 is recorded and the service responds:

```
201 Created
Content-Type: application/json

{
  "claim_reference": "CLM-2026-000317",
  "status": "recorded"
}
```

`claim_reference` matches the pattern `CLM-YYYY-NNNNNN`, where `YYYY` is the calendar year in which the notification was recorded and `NNNNNN` is a zero padded sequence. A claim reference is unique across all recorded notifications and is never reissued. It is the value the claims handler quotes and the value every downstream system keys on.

`status` is `recorded` on every success response this contract defines. It exists because the portal displays it and because a future state that is not `recorded` is foreseeable. Callers must not treat it as constant.

A refused notification is never recorded and no claim reference is issued. There is no partial outcome: either a notification exists with a reference, or nothing was written.

## 4. Validation



### 4.1 Evaluation order

Identifiers name the rules; they do not rank them. A notification can
fail more than one rule and the caller sees one code, so evaluation
order is caller-visible.

Rules are evaluated in this order, stopping at the first failure:

V-1, V-7, V-6, V-2, V-3, V-5, V-4.

The order reports the refusal the handler should act on, from
policy-level facts to notification-level facts. V-1 short circuits: if
it fails, no rule that reads a policy field is evaluated (WI-0142,
AC-4). V-7 applies only when `cancellation_date` is not null (WI-0158,
AC-3); when it is null the rule is skipped. Where a policy is cancelled
and the loss also falls outside the original term, the code is
`POLICY_CANCELLED`, not `LOSS_AFTER_EXPIRY` (WI-0158, AC-4). A duplicate
is reported before inception, expiry, type, or amount. Type is reported
before amount.

### 4.2 Rule table


| ID  | Condition                                                                | Code                     | Status |
| --- | ------------------------------------------------------------------------ | ------------------------ | ------ |
| V-1 | `policy_number` exists in the policy master                              | `POLICY_NOT_FOUND`       | 422    |
| V-2 | `loss_date` >= policy `effective_date`                                   | `LOSS_BEFORE_INCEPTION`  | 422    |
| V-3 | `loss_date` <= policy `expiry_date`                                      | `LOSS_AFTER_EXPIRY`      | 422    |
| V-4 | `estimated_amount` <= policy `limit`                                     | `AMOUNT_EXCEEDS_LIMIT`   | 422    |
| V-5 | `claim_type` permitted on the policy's product                           | `TYPE_NOT_COVERED`       | 422    |
| V-6 | `policy_number, loss_date, claim_type` is unique                         | `DUPLICATE_NOTIFICATION` | 409    |
| V-7 | `cancellation_date` is null, or `loss_date` < policy `cancellation_date` | `POLICY_CANCELLED`       | 422    |


Boundaries are inclusive as written except for V-7. A loss on the
inception date is covered (WI-0142, AC-3). An amount equal to the limit
is within cover. A loss on the cancellation date is not covered:
cancellation takes effect at the start of that date (WI-0158, AC-2).

`policy_number` is matched exactly, including case. A `claim_type` not
in 2.3, or an `estimated_amount` that is not exactly two decimal places,
cannot be interpreted (`MALFORMED_REQUEST`) and is not evaluated against
V-5 or V-4. Amounts are not rounded.

## 5. Error envelope

Every refusal uses this envelope. Status differs; shape does not.

```
{
  "code": "...",
  "message": "...",
  "detail": { }
}
```

`code` is stable. Callers branch on it. Defined codes are those in
section 4.2, `MALFORMED_REQUEST`, and `POLICY_LOOKUP_FAILED`. Unknown
codes fall through to default handling (section 1).

`message` is not stable. Callers must not parse or branch on it.

`detail` is always an object. Keys depend on `code`. A caller may
rely on a key only when this table promises it for that `code`. Ignore
any other key.

`rule` is present only when a section 4 rule decided the refusal.
It is absent when no rule ran. Callers must not require it.


| Code                     | Promised `detail` keys                                        |
| ------------------------ | ------------------------------------------------------------- |
| `POLICY_NOT_FOUND`       | `policy_number`                                               |
| `LOSS_BEFORE_INCEPTION`  | `loss_date`, `effective_date`                                 |
| `LOSS_AFTER_EXPIRY`      | `loss_date`, `expiry_date`                                    |
| `AMOUNT_EXCEEDS_LIMIT`   | `estimated_amount`, `limit`                                   |
| `TYPE_NOT_COVERED`       | `claim_type`, `permitted_claim_types`                         |
| `DUPLICATE_NOTIFICATION` | `policy_number`, `loss_date`, `claim_type`, `claim_reference` |
| `POLICY_CANCELLED`       | `loss_date`, `cancellation_date`                              |
| `MALFORMED_REQUEST`      | `issue`; `field` when the failure names one field             |
| `POLICY_LOOKUP_FAILED`   | `reason`                                                      |


`issue` is one of `not_json`, `missing`, `wrong_type`, `unknown_field`,
`invalid_value`. `reason` is one of `timeout`, `unreachable`,
`unparsable`. `POLICY_LOOKUP_FAILED` is not `POLICY_NOT_FOUND`: the
master did not answer, the caller did nothing wrong, and the same
request may succeed later.

### 5.1 Rule failure

```
422 Unprocessable Entity

{
  "code": "LOSS_BEFORE_INCEPTION",
  "rule": "V-2",
  "message": "The loss date precedes policy inception.",
  "detail": {
    "loss_date": "2026-02-20",
    "effective_date": "2026-03-15"
  }
}
```



### 5.2 Request the service could not interpret

```
400 Bad Request

{
  "code": "MALFORMED_REQUEST",
  "message": "The request body could not be interpreted.",
  "detail": {
    "field": "estimated_amount",
    "issue": "missing"
  }
}
```



### 5.3 Policy master that did not answer

Status depends on `reason` and is fixed in section 6.

```
504 Gateway Timeout

{
  "code": "POLICY_LOOKUP_FAILED",
  "message": "The policy master did not produce a usable answer.",
  "detail": {
    "reason": "timeout"
  }
}
```



## 6. Status code mapping

Every refusal this service produces appears in this table. Status is a
function of `code`, and for `POLICY_LOOKUP_FAILED` of `reason`.


| Condition                            | Code                                   | Status |
| ------------------------------------ | -------------------------------------- | ------ |
| Request cannot be interpreted        | `MALFORMED_REQUEST`                    | 400    |
| Duplicate of a recorded notification | `DUPLICATE_NOTIFICATION`               | 409    |
| Policy not in the master             | `POLICY_NOT_FOUND`                     | 422    |
| Loss before inception                | `LOSS_BEFORE_INCEPTION`                | 422    |
| Loss after expiry                    | `LOSS_AFTER_EXPIRY`                    | 422    |
| Amount exceeds limit                 | `AMOUNT_EXCEEDS_LIMIT`                 | 422    |
| Claim type not covered               | `TYPE_NOT_COVERED`                     | 422    |
| Policy cancelled                     | `POLICY_CANCELLED`                     | 422    |
| Policy master timed out              | `POLICY_LOOKUP_FAILED` (`timeout`)     | 504    |
| Policy master unreachable            | `POLICY_LOOKUP_FAILED` (`unreachable`) | 503    |
| Policy master response unparsable    | `POLICY_LOOKUP_FAILED` (`unparsable`)  | 502    |


