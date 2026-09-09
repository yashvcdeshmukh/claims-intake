"""Map parse, rule, and lookup outcomes onto contract sections 5 and 6.

Status is a function of `code`, and for `POLICY_LOOKUP_FAILED` of `reason`.
This module is that function. It does not decide whether a notification is
admissible; it names the status and envelope the contract already assigned.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import ValidationError

from claims.models import ClaimRecord, NotificationRequest, Policy, RuleFailure
from claims.policy_client import LookupFailureReason, PolicyClient, PolicyLookupFailed
from claims.repository import NotificationRepository

# Contract section 6. A code that is not a key here has no status this service
# is allowed to return.
STATUS_FOR_CODE: dict[str, int] = {
    "MALFORMED_REQUEST": 400,
    "DUPLICATE_NOTIFICATION": 409,
    "POLICY_NOT_FOUND": 422,
    "LOSS_BEFORE_INCEPTION": 422,
    "LOSS_AFTER_EXPIRY": 422,
    "AMOUNT_EXCEEDS_LIMIT": 422,
    "TYPE_NOT_COVERED": 422,
    "POLICY_CANCELLED": 422,
}

STATUS_FOR_LOOKUP_REASON: dict[LookupFailureReason, int] = {
    "timeout": 504,
    "unreachable": 503,
    "unparsable": 502,
}

# `message` is not stable. Callers must not branch on it. The three strings that
# appear in section 5 are copied; the rest are the same kind of sentence.
MESSAGE_FOR_CODE: dict[str, str] = {
    "MALFORMED_REQUEST": "The request body could not be interpreted.",
    "POLICY_NOT_FOUND": "No policy exists with that number.",
    "LOSS_BEFORE_INCEPTION": "The loss date precedes policy inception.",
    "LOSS_AFTER_EXPIRY": "The loss date falls after policy expiry.",
    "AMOUNT_EXCEEDS_LIMIT": "The estimated amount exceeds the policy limit.",
    "TYPE_NOT_COVERED": "The claim type is not covered on this product.",
    "DUPLICATE_NOTIFICATION": "A notification for this loss has already been recorded.",
    "POLICY_CANCELLED": "The policy was cancelled before the loss date.",
    "POLICY_LOOKUP_FAILED": "The policy master did not produce a usable answer.",
}

_POLICY_DETAIL_CODES = frozenset(
    {
        "LOSS_BEFORE_INCEPTION",
        "LOSS_AFTER_EXPIRY",
        "AMOUNT_EXCEEDS_LIMIT",
        "TYPE_NOT_COVERED",
        "POLICY_CANCELLED",
    }
)


@dataclass(frozen=True)
class MappedResponse:
    """A status and a JSON-ready body. Routes wrap this; they do not reshape it."""

    status_code: int
    body: dict[str, object]


def error_envelope(
    *,
    code: str,
    message: str,
    detail: dict[str, object],
    rule: str | None = None,
) -> dict[str, object]:
    """Section 5 shape. `rule` is omitted when no section 4 rule decided."""

    body: dict[str, object] = {"code": code}
    if rule is not None:
        body["rule"] = rule
    body["message"] = message
    body["detail"] = detail
    return body


def map_recorded(record: ClaimRecord) -> MappedResponse:
    """Section 3. Not the error envelope."""

    return MappedResponse(
        status_code=201,
        body={"claim_reference": record.claim_reference, "status": "recorded"},
    )


def parse_notification(raw: bytes) -> NotificationRequest | MappedResponse:
    """Interpret the body, or return the 400 envelope section 6 assigns.

    JSON that cannot be decoded is `not_json`. A body that decodes and then
    fails `NotificationRequest` is classified from the first Pydantic error.
    """

    try:
        payload: object = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _malformed("not_json")
    try:
        return NotificationRequest.model_validate(payload)
    except ValidationError as exc:
        issue, field = _issue_from_pydantic(exc)
        return _malformed(issue, field)


def map_rule_failure(
    failure: RuleFailure,
    notification: NotificationRequest,
    policy_client: PolicyClient,
    repository: NotificationRepository,
) -> MappedResponse:
    """Section 6 status for a rule code, with the detail keys section 5 promises."""

    status = STATUS_FOR_CODE[failure.code]
    detail = _rule_detail(failure, notification, policy_client, repository)
    return MappedResponse(
        status_code=status,
        body=error_envelope(
            code=failure.code,
            rule=failure.rule,
            message=MESSAGE_FOR_CODE[failure.code],
            detail=detail,
        ),
    )


def map_lookup_failed(failed: PolicyLookupFailed) -> MappedResponse:
    """Section 6 status for `reason`. No `rule`: no section 4 rule ran."""

    return MappedResponse(
        status_code=STATUS_FOR_LOOKUP_REASON[failed.reason],
        body=error_envelope(
            code="POLICY_LOOKUP_FAILED",
            message=MESSAGE_FOR_CODE["POLICY_LOOKUP_FAILED"],
            detail={"reason": failed.reason},
        ),
    )


def _malformed(issue: str, field: str | None = None) -> MappedResponse:
    detail: dict[str, object] = {"issue": issue}
    if field is not None:
        detail["field"] = field
    return MappedResponse(
        status_code=STATUS_FOR_CODE["MALFORMED_REQUEST"],
        body=error_envelope(
            code="MALFORMED_REQUEST",
            message=MESSAGE_FOR_CODE["MALFORMED_REQUEST"],
            detail=detail,
        ),
    )


def _issue_from_pydantic(exc: ValidationError) -> tuple[str, str | None]:
    """Map the first Pydantic error onto section 5 `issue` / `field`."""

    errors = exc.errors()
    error = errors[0]
    error_type = error["type"]
    field = _field_from_loc(error["loc"])
    if error_type == "missing":
        return "missing", field
    if error_type == "extra_forbidden":
        return "unknown_field", field
    if error_type.endswith("_type"):
        if error_type in {"model_type", "model_attributes_type"}:
            return "wrong_type", None
        return "wrong_type", field
    return "invalid_value", field


def _field_from_loc(loc: tuple[int | str, ...]) -> str | None:
    if len(loc) == 1 and isinstance(loc[0], str):
        return loc[0]
    return None


def _rule_detail(
    failure: RuleFailure,
    notification: NotificationRequest,
    policy_client: PolicyClient,
    repository: NotificationRepository,
) -> dict[str, object]:
    if failure.code == "POLICY_NOT_FOUND":
        return {"policy_number": notification.policy_number}
    if failure.code == "DUPLICATE_NOTIFICATION":
        existing = repository.find_matching(
            notification.policy_number,
            notification.loss_date,
            notification.claim_type,
        )
        detail: dict[str, object] = {
            "policy_number": notification.policy_number,
            "loss_date": _iso(notification.loss_date),
            "claim_type": notification.claim_type,
        }
        if existing is not None:
            detail["claim_reference"] = existing.claim_reference
        return detail
    if failure.code in _POLICY_DETAIL_CODES:
        policy = Policy.from_record(policy_client.get_policy(notification.policy_number))
        return _policy_detail(failure.code, notification, policy)
    raise KeyError(failure.code)


def _policy_detail(
    code: str,
    notification: NotificationRequest,
    policy: Policy,
) -> dict[str, object]:
    if code == "LOSS_BEFORE_INCEPTION":
        return {
            "loss_date": _iso(notification.loss_date),
            "effective_date": _iso(policy.effective_date),
        }
    if code == "LOSS_AFTER_EXPIRY":
        return {
            "loss_date": _iso(notification.loss_date),
            "expiry_date": _iso(policy.expiry_date),
        }
    if code == "AMOUNT_EXCEEDS_LIMIT":
        return {
            "estimated_amount": _money(notification.estimated_amount),
            "limit": _money(policy.limit),
        }
    if code == "TYPE_NOT_COVERED":
        return {
            "claim_type": notification.claim_type,
            "permitted_claim_types": list(policy.permitted_claim_types),
        }
    if code == "POLICY_CANCELLED":
        cancellation = policy.cancellation_date
        assert cancellation is not None
        return {
            "loss_date": _iso(notification.loss_date),
            "cancellation_date": _iso(cancellation),
        }
    raise KeyError(code)


def _iso(value: date) -> str:
    return value.isoformat()


def _money(value: Decimal) -> str:
    return f"{value:.2f}"
