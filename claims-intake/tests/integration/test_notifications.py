"""POST /notifications against contract sections 3, 5, and 6.

A fixture returns a fresh app. Shared store state would make the suite
order-dependent; a duplicate test that saw another test's 201 is a false pass.
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

DATA = Path(__file__).resolve().parents[2] / "data"
CLAIM_REFERENCE = re.compile(r"^CLM-\d{4}-\d{6}$")


def _payloads(filename: str) -> dict[str, dict[str, object]]:
    rows = json.loads((DATA / filename).read_text())
    return {row["id"]: row["payload"] for row in rows}


VALID = _payloads("fnol_valid.json")
INVALID = _payloads("fnol_invalid.json")
EDGE = _payloads("fnol_edge.json")


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _post(client: TestClient, payload: dict[str, object]) -> httpx.Response:
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
    assert set(body) <= {"code", "rule", "message", "detail"}
    assert {"code", "message", "detail"} <= set(body)
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


def test_accepted_notification_is_recorded_only_once(client: TestClient) -> None:
    first = _post(client, VALID["VALID-01"])
    second = _post(client, VALID["VALID-01"])
    assert first.status_code == 201
    body = _assert_refusal(
        second,
        status=409,
        code="DUPLICATE_NOTIFICATION",
        rule="V-6",
    )
    assert body["detail"]["claim_reference"] == _body(first)["claim_reference"]
    assert body["detail"]["policy_number"] == "MOT-4471"
    assert body["detail"]["loss_date"] == "2026-04-02"
    assert body["detail"]["claim_type"] == "collision"


@pytest.mark.parametrize(
    ("raw", "issue", "field"),
    [
        pytest.param(b"not json", "not_json", None, id="not_json"),
        pytest.param(b"", "not_json", None, id="empty_body"),
    ],
)
def test_body_that_is_not_json_is_malformed(
    client: TestClient,
    raw: bytes,
    issue: str,
    field: str | None,
) -> None:
    response = client.post(
        "/notifications",
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    body = _assert_refusal(response, status=400, code="MALFORMED_REQUEST", rule=None)
    assert body["detail"]["issue"] == issue
    if field is None:
        assert "field" not in body["detail"]
    else:
        assert body["detail"]["field"] == field


@pytest.mark.parametrize(
    ("payload", "issue", "field"),
    [
        pytest.param(EDGE["EDGE-08"], "missing", "estimated_amount", id="EDGE-08_missing"),
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
def test_uninterpretable_payload_is_malformed(
    client: TestClient,
    payload: dict[str, object],
    issue: str,
    field: str,
) -> None:
    body = _assert_refusal(
        _post(client, payload),
        status=400,
        code="MALFORMED_REQUEST",
        rule=None,
    )
    assert body["detail"]["issue"] == issue
    assert body["detail"]["field"] == field


def test_json_array_is_malformed_without_a_field(client: TestClient) -> None:
    response = client.post("/notifications", json=[{"policy_number": "MOT-4471"}])
    body = _assert_refusal(response, status=400, code="MALFORMED_REQUEST", rule=None)
    assert body["detail"]["issue"] == "wrong_type"
    assert "field" not in body["detail"]


@pytest.mark.parametrize(
    ("payload_id", "code", "rule", "status", "detail_keys"),
    [
        pytest.param(
            "INVALID-01",
            "POLICY_NOT_FOUND",
            "V-1",
            422,
            {"policy_number"},
            id="V-1_policy_not_found",
        ),
        pytest.param(
            "INVALID-02",
            "LOSS_BEFORE_INCEPTION",
            "V-2",
            422,
            {"loss_date", "effective_date"},
            id="V-2_before_inception",
        ),
        pytest.param(
            "INVALID-03",
            "LOSS_AFTER_EXPIRY",
            "V-3",
            422,
            {"loss_date", "expiry_date"},
            id="V-3_after_expiry",
        ),
        pytest.param(
            "INVALID-04",
            "AMOUNT_EXCEEDS_LIMIT",
            "V-4",
            422,
            {"estimated_amount", "limit"},
            id="V-4_over_limit",
        ),
        pytest.param(
            "INVALID-05",
            "TYPE_NOT_COVERED",
            "V-5",
            422,
            {"claim_type", "permitted_claim_types"},
            id="V-5_type_not_covered",
        ),
        pytest.param(
            "INVALID-07",
            "POLICY_CANCELLED",
            "V-7",
            422,
            {"loss_date", "cancellation_date"},
            id="V-7_cancelled",
        ),
    ],
)
def test_rule_failure_uses_the_section_6_status(
    client: TestClient,
    payload_id: str,
    code: str,
    rule: str,
    status: int,
    detail_keys: set[str],
) -> None:
    body = _assert_refusal(
        _post(client, INVALID[payload_id]),
        status=status,
        code=code,
        rule=rule,
    )
    assert set(body["detail"]) == detail_keys


def test_v2_detail_names_the_dates_the_rule_compared(client: TestClient) -> None:
    body = _assert_refusal(
        _post(client, INVALID["INVALID-02"]),
        status=422,
        code="LOSS_BEFORE_INCEPTION",
        rule="V-2",
    )
    assert body["detail"]["loss_date"] == "2026-02-20"
    assert body["detail"]["effective_date"] == "2026-03-15"


def test_v4_detail_serializes_amounts_as_two_place_strings(client: TestClient) -> None:
    body = _assert_refusal(
        _post(client, INVALID["INVALID-04"]),
        status=422,
        code="AMOUNT_EXCEEDS_LIMIT",
        rule="V-4",
    )
    assert body["detail"]["estimated_amount"] == "14500.00"
    assert body["detail"]["limit"] == "10000.00"


def test_duplicate_catalog_payload_is_409_after_the_original_is_recorded(
    client: TestClient,
) -> None:
    recorded = _post(client, VALID["VALID-01"])
    body = _assert_refusal(
        _post(client, INVALID["INVALID-06"]),
        status=409,
        code="DUPLICATE_NOTIFICATION",
        rule="V-6",
    )
    assert body["detail"]["claim_reference"] == _body(recorded)["claim_reference"]


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        pytest.param("timeout", 504, id="timeout"),
        pytest.param("unreachable", 503, id="unreachable"),
        pytest.param("unparsable", 502, id="unparsable"),
    ],
)
def test_policy_lookup_failed_maps_reason_to_section_6_status(
    reason: LookupFailureReason,
    status: int,
) -> None:
    client = TestClient(create_app(policy_client=StubPolicyClient(fail_with=reason)))
    body = _assert_refusal(
        _post(client, VALID["VALID-01"]),
        status=status,
        code="POLICY_LOOKUP_FAILED",
        rule=None,
    )
    assert body["detail"] == {"reason": reason}
