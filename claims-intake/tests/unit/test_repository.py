"""Recording, claim references, and WI-0151 duplicate detection.

Fixtures return a fresh repository and a fresh notification. Nothing here
depends on another test having run.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from claims.models import NotificationRequest
from claims.policy_client import StubPolicyClient
from claims.repository import NotificationRepository

CLAIM_REFERENCE = re.compile(r"^CLM-\d{4}-\d{6}$")


def make_notification(**overrides: object) -> NotificationRequest:
    payload: dict[str, object] = {
        "policy_number": "MOT-4471",
        "loss_date": date(2026, 4, 2),
        "claim_type": "collision",
        "estimated_amount": Decimal("4200.00"),
        "description": "Rear ended at a junction.",
    }
    payload.update(overrides)
    return NotificationRequest.model_validate(payload)


@pytest.fixture
def repository() -> NotificationRepository:
    return NotificationRepository()


@pytest.fixture
def notification(policy_client: StubPolicyClient) -> NotificationRequest:
    record = policy_client.get_policy("MOT-4471")
    return make_notification(policy_number=record.policy_number)


def test_record_returns_a_claim_reference_in_contract_format(
    repository: NotificationRepository,
    notification: NotificationRequest,
) -> None:
    recorded = repository.record(notification)
    assert CLAIM_REFERENCE.fullmatch(recorded.claim_reference)
    year = datetime.now(tz=UTC).year
    assert recorded.claim_reference.startswith(f"CLM-{year}-")
    assert recorded.policy_number == notification.policy_number
    assert recorded.loss_date == notification.loss_date
    assert recorded.claim_type == notification.claim_type
    assert recorded.estimated_amount == notification.estimated_amount


def test_issue_claim_reference_matches_contract_format(
    repository: NotificationRepository,
) -> None:
    reference = repository.issue_claim_reference()
    assert CLAIM_REFERENCE.fullmatch(reference)


def test_recorded_references_are_unique(
    repository: NotificationRepository,
    notification: NotificationRequest,
) -> None:
    first = repository.record(notification)
    second = repository.record(
        make_notification(loss_date=date(2026, 4, 3)),
    )
    assert first.claim_reference != second.claim_reference


def test_find_matching_returns_the_recorded_notification(
    repository: NotificationRepository,
    notification: NotificationRequest,
) -> None:
    recorded = repository.record(notification)
    found = repository.find_matching(
        notification.policy_number,
        notification.loss_date,
        notification.claim_type,
    )
    assert found is not None
    assert found.claim_reference == recorded.claim_reference


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"policy_number": "MOT-4472"}, id="policy_number_differs"),
        pytest.param({"loss_date": date(2026, 4, 3)}, id="loss_date_differs"),
        pytest.param({"claim_type": "theft"}, id="claim_type_differs"),
    ],
)
def test_two_of_three_matching_fields_is_not_a_duplicate(
    repository: NotificationRepository,
    notification: NotificationRequest,
    overrides: dict[str, object],
) -> None:
    repository.record(notification)
    other = make_notification(**overrides)
    assert (
        repository.find_matching(other.policy_number, other.loss_date, other.claim_type)
        is None
    )


def test_rejected_notification_is_not_a_duplicate(
    repository: NotificationRepository,
    notification: NotificationRequest,
) -> None:
    """WI-0151 AC-3: a refused submission was never written."""
    assert (
        repository.find_matching(
            notification.policy_number,
            notification.loss_date,
            notification.claim_type,
        )
        is None
    )
    recorded = repository.record(notification)
    found = repository.find_matching(
        notification.policy_number,
        notification.loss_date,
        notification.claim_type,
    )
    assert found is not None
    assert found.claim_reference == recorded.claim_reference
