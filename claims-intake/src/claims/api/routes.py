"""HTTP surface for the claims intake service.

This layer does three things and no more: it parses the request, it calls the
service, and it maps the outcome to a status code. It holds no rule logic. A rule
that appears here is a rule the service layer cannot be tested for.

Day 4 lab. Implement against `docs/api-contract.md` sections 5 and 6.
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from claims.models import ClaimRecord, NotificationRequest, Policy, RuleFailure
from claims.policy_client import (
    LookupFailureReason,
    PolicyClient,
    PolicyLookupFailed,
    StubPolicyClient,
)
from claims.repository import NotificationRepository
from claims.service import submit_notification

type MalformedIssue = Literal[
    "not_json",
    "missing",
    "wrong_type",
    "unknown_field",
    "invalid_value",
]

RULE_STATUS: dict[str, int] = {
    "POLICY_NOT_FOUND": 422,
    "LOSS_BEFORE_INCEPTION": 422,
    "LOSS_AFTER_EXPIRY": 422,
    "AMOUNT_EXCEEDS_LIMIT": 422,
    "TYPE_NOT_COVERED": 422,
    "POLICY_CANCELLED": 422,
    "DUPLICATE_NOTIFICATION": 409,
}

LOOKUP_STATUS: dict[LookupFailureReason, int] = {
    "timeout": 504,
    "unreachable": 503,
    "unparsable": 502,
}

RULE_MESSAGES: dict[str, str] = {
    "POLICY_NOT_FOUND": "No policy exists with the given policy number.",
    "LOSS_BEFORE_INCEPTION": "The loss date precedes policy inception.",
    "LOSS_AFTER_EXPIRY": "The loss date falls after policy expiry.",
    "AMOUNT_EXCEEDS_LIMIT": "The estimated amount exceeds the policy limit.",
    "TYPE_NOT_COVERED": "The claim type is not covered on this product.",
    "DUPLICATE_NOTIFICATION": "A notification for this loss has already been recorded.",
    "POLICY_CANCELLED": "The policy was cancelled before the loss date.",
}

_WRONG_TYPE_PYDANTIC = frozenset(
    {
        "string_type",
        "decimal_type",
        "date_type",
        "dict_type",
        "list_type",
        "int_type",
        "float_type",
        "bool_type",
        "model_type",
        "tuple_type",
        "set_type",
        "is_instance_of",
    }
)


def _issue_from_pydantic_type(error_type: str) -> MalformedIssue:
    if error_type == "missing":
        return "missing"
    if error_type == "extra_forbidden":
        return "unknown_field"
    if error_type in _WRONG_TYPE_PYDANTIC or error_type.endswith("_type"):
        return "wrong_type"
    return "invalid_value"


def _malformed_response(detail: dict[str, object]) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "code": "MALFORMED_REQUEST",
            "message": "The request body could not be interpreted.",
            "detail": detail,
        },
    )


def _malformed(issue: MalformedIssue, field: str | None = None) -> JSONResponse:
    detail: dict[str, object] = {"issue": issue}
    if field is not None:
        detail["field"] = field
    return _malformed_response(detail)


def _malformed_from_validation(exc: ValidationError) -> JSONResponse:
    errors = exc.errors()
    if not errors:
        return _malformed("invalid_value")
    error = errors[0]
    issue = _issue_from_pydantic_type(str(error["type"]))
    loc = error["loc"]
    field: str | None = None
    if loc and isinstance(loc[0], str):
        field = loc[0]
    return _malformed(issue, field)


def _lookup_failed(reason: LookupFailureReason) -> JSONResponse:
    return JSONResponse(
        status_code=LOOKUP_STATUS[reason],
        content={
            "code": "POLICY_LOOKUP_FAILED",
            "message": "The policy master did not produce a usable answer.",
            "detail": {"reason": reason},
        },
    )


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
        claim_reference = existing.claim_reference if existing is not None else ""
        return {
            "policy_number": notification.policy_number,
            "loss_date": notification.loss_date.isoformat(),
            "claim_type": notification.claim_type,
            "claim_reference": claim_reference,
        }

    policy = Policy.from_record(policy_client.get_policy(notification.policy_number))
    if failure.code == "LOSS_BEFORE_INCEPTION":
        return {
            "loss_date": notification.loss_date.isoformat(),
            "effective_date": policy.effective_date.isoformat(),
        }
    if failure.code == "LOSS_AFTER_EXPIRY":
        return {
            "loss_date": notification.loss_date.isoformat(),
            "expiry_date": policy.expiry_date.isoformat(),
        }
    if failure.code == "AMOUNT_EXCEEDS_LIMIT":
        return {
            "estimated_amount": str(notification.estimated_amount),
            "limit": str(policy.limit),
        }
    if failure.code == "TYPE_NOT_COVERED":
        return {
            "claim_type": notification.claim_type,
            "permitted_claim_types": list(policy.permitted_claim_types),
        }
    cancellation = (
        policy.cancellation_date.isoformat() if policy.cancellation_date else None
    )
    return {
        "loss_date": notification.loss_date.isoformat(),
        "cancellation_date": cancellation,
    }


def _rule_failure_response(
    failure: RuleFailure,
    notification: NotificationRequest,
    policy_client: PolicyClient,
    repository: NotificationRepository,
) -> JSONResponse:
    return JSONResponse(
        status_code=RULE_STATUS[failure.code],
        content={
            "code": failure.code,
            "rule": failure.rule,
            "message": RULE_MESSAGES[failure.code],
            "detail": _rule_detail(failure, notification, policy_client, repository),
        },
    )


def create_app(
    policy_client: PolicyClient | None = None,
    repository: NotificationRepository | None = None,
) -> FastAPI:
    """Build the HTTP app with injected policy client and store.

    Tests pass fresh instances so one recorded notification cannot leak into
    another case. The module-level `app` uses the defaults for a running server.
    """
    client = policy_client or StubPolicyClient()
    store = repository or NotificationRepository()
    application = FastAPI(title="Claims Intake Service")

    @application.post("/notifications")
    async def post_notifications(request: Request) -> JSONResponse:
        try:
            payload: object = json.loads(await request.body())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _malformed("not_json")
        if not isinstance(payload, dict):
            return _malformed("wrong_type")
        try:
            notification = NotificationRequest.model_validate(payload)
        except ValidationError as exc:
            return _malformed_from_validation(exc)

        try:
            result = submit_notification(notification, client, store)
        except PolicyLookupFailed as exc:
            return _lookup_failed(exc.reason)

        if isinstance(result, ClaimRecord):
            return JSONResponse(
                status_code=201,
                content={
                    "claim_reference": result.claim_reference,
                    "status": "recorded",
                },
            )
        return _rule_failure_response(result, notification, client, store)

    return application


app = create_app()
