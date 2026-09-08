"""NotificationRequest, Policy, RuleFailure, and ClaimRecord against the contract.

HTTP status and rule codes are not this layer. A successful parse is a typed
object; a failure is pydantic.ValidationError (contract section 2.4).
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from claims.models import (
    ClaimRecord,
    NotificationRequest,
    Policy,
    RuleFailure,
)
from claims.policy_client import StubPolicyClient

DATA = Path(__file__).resolve().parents[2] / "data"

WIRE: dict[str, object] = {
    "policy_number": "MOT-4471",
    "loss_date": "2026-04-02",
    "claim_type": "collision",
    "estimated_amount": "4200.00",
    "description": "Rear ended at a junction.",
}


def _wire(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {**WIRE}
    payload.update(overrides)
    return payload


def _payloads(filename: str) -> dict[str, dict[str, object]]:
    rows = json.loads((DATA / filename).read_text())
    return {row["id"]: row["payload"] for row in rows}


EDGE = _payloads("fnol_edge.json")
INVALID = _payloads("fnol_invalid.json")


@pytest.mark.parametrize(
    ("payload", "expected_description"),
    [
        pytest.param(WIRE, "Rear ended at a junction.", id="description_present"),
        pytest.param(
            {key: value for key, value in WIRE.items() if key != "description"},
            None,
            id="description_omitted",
        ),
        pytest.param(_wire(description=None), None, id="description_null"),
    ],
)
def test_notification_request_accepts_a_well_formed_payload(
    payload: dict[str, object],
    expected_description: str | None,
) -> None:
    notification = NotificationRequest.model_validate(payload)
    assert notification.policy_number == "MOT-4471"
    assert notification.loss_date == date(2026, 4, 2)
    assert notification.claim_type == "collision"
    assert notification.estimated_amount == Decimal("4200.00")
    assert notification.description is expected_description


@pytest.mark.parametrize(
    "claim_type",
    [
        pytest.param("collision", id="collision"),
        pytest.param("theft", id="theft"),
        pytest.param("glass", id="glass"),
        pytest.param("liability", id="liability"),
        pytest.param("weather", id="weather"),
    ],
)
def test_notification_request_accepts_each_vocabulary_claim_type(claim_type: str) -> None:
    notification = NotificationRequest.model_validate(_wire(claim_type=claim_type))
    assert notification.claim_type == claim_type


def test_notification_request_preserves_policy_number_case() -> None:
    notification = NotificationRequest.model_validate(_wire(policy_number="mot-4471"))
    assert notification.policy_number == "mot-4471"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {key: value for key, value in WIRE.items() if key != "policy_number"},
            id="missing_policy_number",
        ),
        pytest.param(
            {key: value for key, value in WIRE.items() if key != "loss_date"},
            id="missing_loss_date",
        ),
        pytest.param(
            {key: value for key, value in WIRE.items() if key != "claim_type"},
            id="missing_claim_type",
        ),
        pytest.param(
            {key: value for key, value in WIRE.items() if key != "estimated_amount"},
            id="missing_estimated_amount",
        ),
        pytest.param(_wire(handler_id="x"), id="unknown_field"),
        pytest.param(_wire(policy_number=""), id="empty_policy_number"),
        pytest.param(_wire(claim_type="flood"), id="claim_type_outside_vocabulary"),
        pytest.param(_wire(claim_type=""), id="empty_claim_type"),
        pytest.param(_wire(loss_date="03/15/2026"), id="loss_date_not_iso"),
        pytest.param(_wire(loss_date="2026-02-30"), id="loss_date_impossible"),
        pytest.param(_wire(estimated_amount="3499.999"), id="amount_three_decimal_places"),
        pytest.param(_wire(estimated_amount="5000"), id="amount_without_cents"),
        pytest.param(_wire(estimated_amount="0.00"), id="amount_zero"),
        pytest.param(_wire(estimated_amount="-1.00"), id="amount_negative"),
        pytest.param(_wire(estimated_amount=4200.00), id="amount_float"),
        pytest.param(_wire(estimated_amount={"value": "4200.00"}), id="amount_wrong_type"),
    ],
)
def test_notification_request_rejects_a_structurally_unacceptable_payload(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        NotificationRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("payload_id", "fails_at_model"),
    [
        pytest.param("EDGE-01", False, id="EDGE-01_inception_day_reaches_rules"),
        pytest.param("EDGE-02", False, id="EDGE-02_amount_at_limit_reaches_rules"),
        pytest.param("EDGE-03", False, id="EDGE-03_expiry_day_reaches_rules"),
        pytest.param("EDGE-04", False, id="EDGE-04_cancellation_day_reaches_rules"),
        pytest.param("EDGE-05", False, id="EDGE-05_before_inception_reaches_rules"),
        pytest.param("EDGE-06", False, id="EDGE-06_over_limit_reaches_rules"),
        pytest.param("EDGE-07", False, id="EDGE-07_lowercase_number_reaches_rules"),
        pytest.param("EDGE-08", True, id="EDGE-08_missing_amount_fails_at_model"),
        pytest.param("EDGE-09", False, id="EDGE-09_type_not_on_product_reaches_rules"),
        pytest.param("EDGE-10", False, id="EDGE-10_cancelled_and_expired_reaches_rules"),
        pytest.param("EDGE-11", True, id="EDGE-11_flood_fails_at_model"),
        pytest.param("EDGE-12", True, id="EDGE-12_three_decimals_fails_at_model"),
        pytest.param("INVALID-01", False, id="INVALID-01_unknown_policy_reaches_rules"),
        pytest.param("INVALID-02", False, id="INVALID-02_before_inception_reaches_rules"),
        pytest.param("INVALID-03", False, id="INVALID-03_after_expiry_reaches_rules"),
        pytest.param("INVALID-04", False, id="INVALID-04_over_limit_reaches_rules"),
        pytest.param("INVALID-05", False, id="INVALID-05_type_not_covered_reaches_rules"),
        pytest.param("INVALID-06", False, id="INVALID-06_duplicate_reaches_rules"),
        pytest.param("INVALID-07", False, id="INVALID-07_cancelled_reaches_rules"),
    ],
)
def test_catalog_payload_fails_at_model_or_survives_to_rules(
    payload_id: str,
    fails_at_model: bool,
) -> None:
    payload = EDGE[payload_id] if payload_id.startswith("EDGE") else INVALID[payload_id]
    if fails_at_model:
        with pytest.raises(ValidationError):
            NotificationRequest.model_validate(payload)
        return
    notification = NotificationRequest.model_validate(payload)
    assert isinstance(notification.loss_date, date)
    assert isinstance(notification.estimated_amount, Decimal)


@pytest.fixture
def policy_client() -> StubPolicyClient:
    return StubPolicyClient()


@pytest.mark.parametrize(
    "policy_number",
    [
        pytest.param("MOT-4471", id="in_force_no_cancellation"),
        pytest.param("MOT-4497", id="cancelled_mid_term"),
    ],
)
def test_policy_from_record_uses_date_and_decimal(
    policy_client: StubPolicyClient,
    policy_number: str,
) -> None:
    policy = Policy.from_record(policy_client.get_policy(policy_number))
    assert isinstance(policy.effective_date, date)
    assert isinstance(policy.expiry_date, date)
    assert isinstance(policy.limit, Decimal)
    if policy.cancellation_date is not None:
        assert isinstance(policy.cancellation_date, date)


def test_policy_cancellation_date_absent_is_none(policy_client: StubPolicyClient) -> None:
    policy = Policy.from_record(policy_client.get_policy("MOT-4471"))
    assert policy.cancellation_date is None


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"limit": 50000.00}, id="limit_float"),
        pytest.param({"effective_date": "2026-03-01"}, id="effective_date_string"),
        pytest.param({"extra": "nope"}, id="unknown_field"),
        pytest.param({"product": ""}, id="empty_product"),
        pytest.param({"policy_number": ""}, id="empty_policy_number"),
    ],
)
def test_policy_rejects_a_constraint_violation(
    policy_client: StubPolicyClient,
    overrides: dict[str, object],
) -> None:
    record = policy_client.get_policy("MOT-4471")
    body: dict[str, object] = {
        "policy_number": record.policy_number,
        "product": record.product,
        "effective_date": record.effective_date,
        "expiry_date": record.expiry_date,
        "cancellation_date": record.cancellation_date,
        "limit": record.limit,
        "permitted_claim_types": record.permitted_claim_types,
    }
    body.update(overrides)
    with pytest.raises(ValidationError):
        Policy.model_validate(body)


def test_rule_failure_is_immutable() -> None:
    failure = RuleFailure(rule="V-2", code="LOSS_BEFORE_INCEPTION")
    assert failure.rule == "V-2"
    assert failure.code == "LOSS_BEFORE_INCEPTION"
    assert is_dataclass(failure)
    assert failure == RuleFailure(rule="V-2", code="LOSS_BEFORE_INCEPTION")
    assert hash(failure) == hash(RuleFailure(rule="V-2", code="LOSS_BEFORE_INCEPTION"))
    assert {field.name for field in fields(failure)} == {"rule", "code"}


@pytest.mark.parametrize(
    "claim_reference",
    [
        pytest.param("CLM-2026-000317", id="section_3_example"),
        pytest.param("CLM-2026-000001", id="first_sequence"),
    ],
)
def test_claim_record_accepts_a_contract_reference(claim_reference: str) -> None:
    recorded = ClaimRecord(
        policy_number="MOT-4471",
        loss_date=date(2026, 4, 2),
        claim_type="collision",
        estimated_amount=Decimal("4200.00"),
        description=None,
        claim_reference=claim_reference,
    )
    assert recorded.claim_reference == claim_reference
    assert recorded.loss_date == date(2026, 4, 2)
    assert recorded.estimated_amount == Decimal("4200.00")


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"claim_reference": "CLM-26-1"}, id="claim_reference_wrong_shape"),
        pytest.param({"loss_date": "2026-04-02"}, id="loss_date_string"),
        pytest.param({"estimated_amount": 4200.00}, id="amount_float"),
        pytest.param({"estimated_amount": Decimal("0.00")}, id="amount_zero"),
        pytest.param({"policy_number": ""}, id="empty_policy_number"),
        pytest.param({"claim_type": "flood"}, id="claim_type_outside_vocabulary"),
        pytest.param({"extra": "nope"}, id="unknown_field"),
    ],
)
def test_claim_record_rejects_a_constraint_violation(overrides: dict[str, object]) -> None:
    body: dict[str, object] = {
        "policy_number": "MOT-4471",
        "loss_date": date(2026, 4, 2),
        "claim_type": "collision",
        "estimated_amount": Decimal("4200.00"),
        "description": None,
        "claim_reference": "CLM-2026-000317",
    }
    body.update(overrides)
    with pytest.raises(ValidationError):
        ClaimRecord.model_validate(body)
