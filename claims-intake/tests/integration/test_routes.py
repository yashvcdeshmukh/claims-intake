"""HTTP tests for POST /notifications.

These exercise the service through the ASGI stack: a request goes in as bytes
and the assertion is on the status line and the JSON body. They never call
`submit_notification` or `evaluate_notification`, because the thing under test
here is the mapping the contract fixes in sections 5 and 6, not the rules.

Every refusal is checked for the envelope shape section 5 promises and for the
`detail` keys and values that make it actionable: a handler who reads only this
response has to know which two facts the service compared.

The app comes from a fixture, so each test gets its own store. A shared store
would make a duplicate test pass because an earlier test recorded the payload.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from claims.api.routes import create_app
from claims.policy_client import LookupFailureReason, StubPolicyClient
from claims.repository import NotificationRepository

CLAIM_REFERENCE = re.compile(r"^CLM-\d{4}-\d{6}$")
DATA = Path(__file__).resolve().parents[2] / "data"


def _payloads(filename: str) -> dict[str, dict[str, Any]]:
    rows = json.loads((DATA / filename).read_text())
    return {row["id"]: row["payload"] for row in rows}


VALID = _payloads("fnol_valid.json")
INVALID = _payloads("fnol_invalid.json")
EDGE = _payloads("fnol_edge.json")


@pytest.fixture
def repository() -> NotificationRepository:
    return NotificationRepository()


@pytest.fixture
def client(
    policy_client: StubPolicyClient,
    repository: NotificationRepository,
) -> TestClient:
    return TestClient(create_app(policy_client, repository))


def _post(client: TestClient, payload: dict[str, object] | list[object]) -> httpx.Response:
    response = client.post("/notifications", json=payload)
    assert isinstance(response, httpx.Response)
    return response


def _body(response: httpx.Response) -> dict[str, Any]:
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _assert_refusal(
    response: httpx.Response,
    *,
    status: int,
    code: str,
    rule: str | None,
) -> dict[str, Any]:
    """Assert the section 5 envelope and return the body for detail assertions.

    `rule` is `None` for a refusal no section 4 rule decided. The contract says
    callers must not require the key, so its absence is asserted rather than
    tolerated: a `rule` on a parse failure would be a fabricated attribution.
    """

    assert response.status_code == status
    body = _body(response)
    assert body["code"] == code
    assert isinstance(body["message"], str)
    assert body["message"]
    assert isinstance(body["detail"], dict)
    if rule is None:
        assert "rule" not in body
    else:
        assert body["rule"] == rule
    assert {"code", "message", "detail"} <= set(body)
    assert set(body) <= {"code", "rule", "message", "detail"}
    return body


def test_accepted_notification_returns_201_with_a_claim_reference(
    client: TestClient,
) -> None:
    response = _post(client, VALID["VALID-01"])
    assert response.status_code == 201
    body = _body(response)
    assert body["status"] == "recorded"
    assert CLAIM_REFERENCE.fullmatch(body["claim_reference"])
    assert set(body) == {"claim_reference", "status"}


@pytest.mark.parametrize(
    ("payload_id", "status", "code", "rule", "detail"),
    [
        pytest.param(
            "INVALID-01",
            422,
            "POLICY_NOT_FOUND",
            "V-1",
            {"policy_number": "MOT-9999"},
            id="V-1_policy_not_found",
        ),
        pytest.param(
            "INVALID-02",
            422,
            "LOSS_BEFORE_INCEPTION",
            "V-2",
            {"loss_date": "2026-02-20", "effective_date": "2026-03-15"},
            id="V-2_loss_before_inception",
        ),
        pytest.param(
            "INVALID-03",
            422,
            "LOSS_AFTER_EXPIRY",
            "V-3",
            {"loss_date": "2026-03-20", "expiry_date": "2026-02-28"},
            id="V-3_loss_after_expiry",
        ),
        pytest.param(
            "INVALID-04",
            422,
            "AMOUNT_EXCEEDS_LIMIT",
            "V-4",
            {"estimated_amount": "14500.00", "limit": "10000.00"},
            id="V-4_amount_exceeds_limit",
        ),
        pytest.param(
            "INVALID-05",
            422,
            "TYPE_NOT_COVERED",
            "V-5",
            {"claim_type": "collision", "permitted_claim_types": ["liability"]},
            id="V-5_type_not_covered",
        ),
        pytest.param(
            "INVALID-07",
            422,
            "POLICY_CANCELLED",
            "V-7",
            {"loss_date": "2026-03-05", "cancellation_date": "2026-02-01"},
            id="V-7_policy_cancelled",
        ),
    ],
)
def test_each_rule_refusal_returns_its_status_code_and_actionable_detail(
    client: TestClient,
    payload_id: str,
    status: int,
    code: str,
    rule: str,
    detail: dict[str, object],
) -> None:
    """One case per rule in section 4.2, except V-6, which needs a prior 201.

    `detail` is compared whole. Asserting the exact object covers both halves of
    the promise in section 5: every key the table promises is present, carrying
    the value the rule actually compared, and no key the caller was told to
    ignore has crept in.
    """

    body = _assert_refusal(
        _post(client, INVALID[payload_id]),
        status=status,
        code=code,
        rule=rule,
    )
    assert body["detail"] == detail


def test_duplicate_of_a_recorded_notification_returns_409_naming_the_first_reference(
    client: TestClient,
) -> None:
    """V-6. INVALID-06 is the resubmission of VALID-01 after a portal timeout.

    The claim reference in `detail` is the one issued to the first submission.
    That is the whole value of the refusal: the caller learns the loss is already
    on file and which reference to quote, instead of retrying.
    """

    recorded = _post(client, VALID["VALID-01"])
    assert recorded.status_code == 201

    body = _assert_refusal(
        _post(client, INVALID["INVALID-06"]),
        status=409,
        code="DUPLICATE_NOTIFICATION",
        rule="V-6",
    )
    assert body["detail"] == {
        "policy_number": "MOT-4471",
        "loss_date": "2026-04-02",
        "claim_type": "collision",
        "claim_reference": _body(recorded)["claim_reference"],
    }


def test_a_notification_is_recorded_once_even_when_the_same_payload_is_sent_twice(
    client: TestClient,
) -> None:
    first = _post(client, VALID["VALID-01"])
    second = _post(client, VALID["VALID-01"])
    assert first.status_code == 201
    body = _assert_refusal(second, status=409, code="DUPLICATE_NOTIFICATION", rule="V-6")
    assert body["detail"]["claim_reference"] == _body(first)["claim_reference"]


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"not json", id="not_json"),
        pytest.param(b"", id="empty_body"),
    ],
)
def test_a_body_that_is_not_json_is_a_400_with_issue_not_json(
    client: TestClient,
    raw: bytes,
) -> None:
    """Only reachable over HTTP: a typed call cannot be handed undecodable bytes.

    `field` is absent because no field failed. The body did.
    """

    response = client.post(
        "/notifications",
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    body = _assert_refusal(response, status=400, code="MALFORMED_REQUEST", rule=None)
    assert body["detail"] == {"issue": "not_json"}


@pytest.mark.parametrize(
    ("payload", "issue", "field"),
    [
        pytest.param(EDGE["EDGE-08"], "missing", "estimated_amount", id="EDGE-08_missing_amount"),
        pytest.param(
            {**VALID["VALID-01"], "handler_id": "x"},
            "unknown_field",
            "handler_id",
            id="unknown_field",
        ),
        pytest.param(
            EDGE["EDGE-11"],
            "invalid_value",
            "claim_type",
            id="EDGE-11_claim_type_outside_vocabulary",
        ),
        pytest.param(
            EDGE["EDGE-12"],
            "invalid_value",
            "estimated_amount",
            id="EDGE-12_three_decimal_places",
        ),
        pytest.param(
            {**VALID["VALID-01"], "estimated_amount": {"value": "4200.00"}},
            "wrong_type",
            "estimated_amount",
            id="amount_wrong_type",
        ),
    ],
)
def test_a_payload_the_service_cannot_interpret_is_a_400_naming_the_field(
    client: TestClient,
    payload: dict[str, object],
    issue: str,
    field: str,
) -> None:
    """Section 2.4: the caller's code is wrong, so the response names what to fix.

    EDGE-11 and EDGE-12 are 400 and not 422: a claim type outside the 2.3
    vocabulary and an amount with three decimal places cannot be interpreted, so
    they are never evaluated against V-5 or V-4.
    """

    body = _assert_refusal(
        _post(client, payload),
        status=400,
        code="MALFORMED_REQUEST",
        rule=None,
    )
    assert body["detail"] == {"issue": issue, "field": field}


def test_a_json_array_is_a_400_with_no_field_to_name(client: TestClient) -> None:
    """The body decoded but is not an object, so no single field is at fault."""

    body = _assert_refusal(
        _post(client, [{"policy_number": "MOT-4471"}]),
        status=400,
        code="MALFORMED_REQUEST",
        rule=None,
    )
    assert body["detail"] == {"issue": "wrong_type"}


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        pytest.param("timeout", 504, id="timeout"),
        pytest.param("unreachable", 503, id="unreachable"),
        pytest.param("unparsable", 502, id="unparsable"),
    ],
)
def test_a_policy_master_that_did_not_answer_maps_its_reason_to_a_5xx(
    repository: NotificationRepository,
    reason: LookupFailureReason,
    status: int,
) -> None:
    """Section 6 gives each reason its own status, and the payload is a valid one.

    A caller retries a timeout, escalates an unreachable host, and reports an
    unparsable answer as a defect in the master. One shared 500 would hide the
    difference. There is no `rule`: no section 4 rule ran, so attributing the
    refusal to one would be a lie about why the request failed.
    """

    client = TestClient(
        create_app(
            policy_client=StubPolicyClient(fail_with=reason),
            repository=repository,
        )
    )
    body = _assert_refusal(
        _post(client, VALID["VALID-01"]),
        status=status,
        code="POLICY_LOOKUP_FAILED",
        rule=None,
    )
    assert body["detail"] == {"reason": reason}
