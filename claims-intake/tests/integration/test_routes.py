"""HTTP tests for POST /notifications.

These exercise the service through the ASGI stack. They do not call submit or
evaluate functions. Assertions are on status, code, and the promised detail
keys that make each refusal actionable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

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
def policy_client() -> StubPolicyClient:
    return StubPolicyClient()


@pytest.fixture
def repository() -> NotificationRepository:
    return NotificationRepository()


@pytest.fixture
def client(
    policy_client: StubPolicyClient,
    repository: NotificationRepository,
) -> TestClient:
    return TestClient(create_app(policy_client, repository))


def test_accepted_notification_returns_201_with_claim_reference(client: TestClient) -> None:
    response = client.post("/notifications", json=VALID["VALID-01"])
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "recorded"
    assert CLAIM_REFERENCE.fullmatch(body["claim_reference"])


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
def test_each_rule_refusal_returns_status_code_and_actionable_detail(
    client: TestClient,
    payload_id: str,
    status: int,
    code: str,
    rule: str,
    detail: dict[str, object],
) -> None:
    response = client.post("/notifications", json=INVALID[payload_id])
    assert response.status_code == status
    body = response.json()
    assert body["code"] == code
    assert body["rule"] == rule
    for key, value in detail.items():
        assert body["detail"][key] == value


def test_duplicate_notification_returns_409_with_existing_claim_reference(
    client: TestClient,
) -> None:
    first = client.post("/notifications", json=VALID["VALID-01"])
    assert first.status_code == 201
    recorded_reference = first.json()["claim_reference"]

    second = client.post("/notifications", json=INVALID["INVALID-06"])
    assert second.status_code == 409
    body = second.json()
    assert body["code"] == "DUPLICATE_NOTIFICATION"
    assert body["rule"] == "V-6"
    assert body["detail"]["policy_number"] == "MOT-4471"
    assert body["detail"]["loss_date"] == "2026-04-02"
    assert body["detail"]["claim_type"] == "collision"
    assert body["detail"]["claim_reference"] == recorded_reference


def test_parse_failure_returns_400_malformed_request(client: TestClient) -> None:
    response = client.post("/notifications", json=EDGE["EDGE-08"])
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "MALFORMED_REQUEST"
    assert "rule" not in body
    assert body["detail"]["issue"] == "missing"
    assert body["detail"]["field"] == "estimated_amount"


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        pytest.param("timeout", 504, id="timeout"),
        pytest.param("unreachable", 503, id="unreachable"),
        pytest.param("unparsable", 502, id="unparsable"),
    ],
)
def test_policy_lookup_failed_maps_reason_to_status(
    policy_client: StubPolicyClient,
    repository: NotificationRepository,
    reason: LookupFailureReason,
    status: int,
) -> None:
    policy_client.fail_with = reason
    client = TestClient(create_app(policy_client, repository))
    response = client.post("/notifications", json=VALID["VALID-01"])
    assert response.status_code == status
    body = response.json()
    assert body["code"] == "POLICY_LOOKUP_FAILED"
    assert "rule" not in body
    assert body["detail"]["reason"] == reason
