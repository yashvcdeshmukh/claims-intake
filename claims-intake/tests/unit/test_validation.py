"""One parametrized test per rule in contract section 4.

Assertions are on ValidationOutcome. HTTP status is section 6 and is not this
layer. Cases use contract vocabulary and the work-item criteria they protect.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from claims.models import ClaimRecord, NotificationRequest, Policy
from claims.policy_client import PolicyLookupFailed, StubPolicyClient
from claims.repository import NotificationRepository
from claims.service import (
    ValidationOutcome,
    evaluate_amount_within_limit,
    evaluate_claim_type_covered,
    evaluate_duplicate_notification,
    evaluate_loss_after_inception,
    evaluate_loss_before_cancellation,
    evaluate_loss_before_expiry,
    evaluate_notification,
    evaluate_policy_exists,
    submit_notification,
)

EFFECTIVE = date(2026, 3, 1)
EXPIRY = date(2027, 2, 28)
CANCELLED_ON = date(2026, 6, 15)
LIMIT = Decimal("50000.00")
PERMITTED = ("collision", "theft", "glass", "liability", "weather")


def make_notification(**overrides: object) -> NotificationRequest:
    payload: dict[str, object] = {
        "policy_number": "MOT-1000",
        "loss_date": date(2026, 4, 2),
        "claim_type": "collision",
        "estimated_amount": Decimal("4200.00"),
    }
    payload.update(overrides)
    return NotificationRequest.model_validate(payload)


def make_policy(**overrides: object) -> Policy:
    payload: dict[str, object] = {
        "policy_number": "MOT-1000",
        "product": "personal_auto_standard",
        "effective_date": EFFECTIVE,
        "expiry_date": EXPIRY,
        "cancellation_date": None,
        "limit": LIMIT,
        "permitted_claim_types": PERMITTED,
    }
    payload.update(overrides)
    return Policy.model_validate(payload)


@pytest.fixture
def repository() -> NotificationRepository:
    return NotificationRepository()


@pytest.mark.parametrize(
    ("policy_number", "expect_pass", "expect_code"),
    [
        pytest.param("MOT-4471", True, None, id="WI-0142_AC-4_policy_exists"),
        pytest.param(
            "MOT-9999",
            False,
            "POLICY_NOT_FOUND",
            id="WI-0142_AC-4_policy_not_found",
        ),
        pytest.param(
            "mot-4471",
            False,
            "POLICY_NOT_FOUND",
            id="policy_number_case_differs",
        ),
    ],
)
def test_v1_policy_exists(
    policy_client: StubPolicyClient,
    policy_number: str,
    expect_pass: bool,
    expect_code: str | None,
) -> None:
    notification = make_notification(policy_number=policy_number)
    outcome = evaluate_policy_exists(notification, policy_client)
    assert outcome.passed is expect_pass
    if expect_pass:
        return
    assert outcome.rule == "V-1"
    assert outcome.code == expect_code
    assert outcome.detail["policy_number"] == policy_number


def test_v1_policy_lookup_failed_is_not_a_rule_refusal(
    policy_client: StubPolicyClient,
) -> None:
    policy_client.fail_with = "timeout"
    with pytest.raises(PolicyLookupFailed) as raised:
        evaluate_policy_exists(make_notification(policy_number="MOT-4471"), policy_client)
    assert raised.value.reason == "timeout"


@pytest.mark.parametrize(
    ("loss_date", "expect_pass"),
    [
        pytest.param(EFFECTIVE - timedelta(days=1), False, id="WI-0142_AC-1_day_before_inception"),
        pytest.param(EFFECTIVE, True, id="WI-0142_AC-3_on_inception"),
        pytest.param(EFFECTIVE + timedelta(days=1), True, id="day_after_inception"),
    ],
)
def test_v2_loss_after_inception(loss_date: date, expect_pass: bool) -> None:
    notification = make_notification(loss_date=loss_date)
    policy = make_policy()
    outcome = evaluate_loss_after_inception(notification, policy)
    assert outcome.passed is expect_pass
    if expect_pass:
        return
    assert outcome.rule == "V-2"
    assert outcome.code == "LOSS_BEFORE_INCEPTION"
    assert outcome.detail["loss_date"] == loss_date
    assert outcome.detail["effective_date"] == EFFECTIVE


@pytest.mark.parametrize(
    ("loss_date", "expect_pass"),
    [
        pytest.param(EXPIRY - timedelta(days=1), True, id="day_before_expiry"),
        pytest.param(EXPIRY, True, id="on_expiry_inclusive"),
        pytest.param(EXPIRY + timedelta(days=1), False, id="day_after_expiry"),
    ],
)
def test_v3_loss_before_expiry(loss_date: date, expect_pass: bool) -> None:
    notification = make_notification(loss_date=loss_date)
    policy = make_policy()
    outcome = evaluate_loss_before_expiry(notification, policy)
    assert outcome.passed is expect_pass
    if expect_pass:
        return
    assert outcome.rule == "V-3"
    assert outcome.code == "LOSS_AFTER_EXPIRY"
    assert outcome.detail["loss_date"] == loss_date
    assert outcome.detail["expiry_date"] == EXPIRY


@pytest.mark.parametrize(
    ("amount", "expect_pass"),
    [
        pytest.param(LIMIT - Decimal("0.01"), True, id="one_cent_under_limit"),
        pytest.param(LIMIT, True, id="amount_equal_to_limit"),
        pytest.param(LIMIT + Decimal("0.01"), False, id="one_cent_over_limit"),
    ],
)
def test_v4_amount_within_limit(amount: Decimal, expect_pass: bool) -> None:
    notification = make_notification(estimated_amount=amount)
    policy = make_policy()
    outcome = evaluate_amount_within_limit(notification, policy)
    assert outcome.passed is expect_pass
    if expect_pass:
        return
    assert outcome.rule == "V-4"
    assert outcome.code == "AMOUNT_EXCEEDS_LIMIT"
    assert outcome.detail["estimated_amount"] == amount
    assert outcome.detail["limit"] == LIMIT


@pytest.mark.parametrize(
    ("claim_type", "permitted", "expect_pass"),
    [
        pytest.param("collision", PERMITTED, True, id="type_on_product"),
        pytest.param(
            "collision",
            ("theft", "glass", "weather", "liability"),
            False,
            id="type_in_vocabulary_not_on_product",
        ),
        pytest.param("theft", ("theft", "glass"), True, id="subset_includes_type"),
    ],
)
def test_v5_claim_type_covered(
    claim_type: str,
    permitted: tuple[str, ...],
    expect_pass: bool,
) -> None:
    notification = make_notification(claim_type=claim_type)
    policy = make_policy(permitted_claim_types=permitted)
    outcome = evaluate_claim_type_covered(notification, policy)
    assert outcome.passed is expect_pass
    if expect_pass:
        return
    assert outcome.rule == "V-5"
    assert outcome.code == "TYPE_NOT_COVERED"
    assert outcome.detail["claim_type"] == claim_type
    assert outcome.detail["permitted_claim_types"] == permitted


@pytest.mark.parametrize(
    ("record_first", "second_overrides", "expect_pass"),
    [
        pytest.param(False, {}, True, id="WI-0151_AC-3_nothing_recorded_is_not_a_duplicate"),
        pytest.param(True, {}, False, id="WI-0151_AC-1_same_three_fields"),
        pytest.param(True, {"policy_number": "MOT-1001"}, True, id="policy_number_differs"),
        pytest.param(True, {"loss_date": date(2026, 4, 3)}, True, id="loss_date_differs"),
        pytest.param(True, {"claim_type": "theft"}, True, id="claim_type_differs"),
    ],
)
def test_v6_duplicate_notification(
    repository: NotificationRepository,
    record_first: bool,
    second_overrides: dict[str, object],
    expect_pass: bool,
) -> None:
    first = make_notification()
    if record_first:
        recorded = repository.record(first)
    else:
        recorded = None
    second = make_notification(**second_overrides)
    outcome = evaluate_duplicate_notification(second, repository)
    assert outcome.passed is expect_pass
    if expect_pass:
        return
    assert recorded is not None
    assert outcome.rule == "V-6"
    assert outcome.code == "DUPLICATE_NOTIFICATION"
    assert outcome.detail["policy_number"] == second.policy_number
    assert outcome.detail["loss_date"] == second.loss_date
    assert outcome.detail["claim_type"] == second.claim_type
    assert outcome.detail["claim_reference"] == recorded.claim_reference


@pytest.mark.parametrize(
    ("cancellation_date", "loss_date", "expect_pass"),
    [
        pytest.param(None, date(2026, 8, 1), True, id="WI-0158_AC-3_cancellation_absent"),
        pytest.param(
            CANCELLED_ON,
            CANCELLED_ON - timedelta(days=1),
            True,
            id="day_before_cancellation",
        ),
        pytest.param(
            CANCELLED_ON,
            CANCELLED_ON,
            False,
            id="WI-0158_AC-2_on_cancellation_date",
        ),
        pytest.param(
            CANCELLED_ON,
            CANCELLED_ON + timedelta(days=1),
            False,
            id="WI-0158_AC-1_day_after_cancellation",
        ),
    ],
)
def test_v7_loss_before_cancellation(
    cancellation_date: date | None,
    loss_date: date,
    expect_pass: bool,
) -> None:
    notification = make_notification(loss_date=loss_date)
    policy = make_policy(cancellation_date=cancellation_date)
    outcome = evaluate_loss_before_cancellation(notification, policy)
    assert outcome.passed is expect_pass
    if expect_pass:
        return
    assert outcome.rule == "V-7"
    assert outcome.code == "POLICY_CANCELLED"
    assert outcome.detail["loss_date"] == loss_date
    assert outcome.detail["cancellation_date"] == cancellation_date


@pytest.mark.parametrize(
    ("policy_number", "loss_date", "record_first", "expect_code"),
    [
        pytest.param(
            "MOT-9999",
            date(2025, 1, 1),
            False,
            "POLICY_NOT_FOUND",
            id="WI-0142_AC-4_not_found_not_inception",
        ),
        pytest.param(
            "MOT-4500",
            date(2026, 1, 8),
            False,
            "POLICY_CANCELLED",
            id="WI-0158_AC-4_cancelled_and_after_expiry",
        ),
        pytest.param(
            "MOT-4471",
            date(2026, 4, 2),
            True,
            "DUPLICATE_NOTIFICATION",
            id="duplicate_reported_before_inception_type_or_amount",
        ),
    ],
)
def test_evaluate_notification_reports_the_first_failure(
    policy_client: StubPolicyClient,
    repository: NotificationRepository,
    policy_number: str,
    loss_date: date,
    record_first: bool,
    expect_code: str,
) -> None:
    notification = make_notification(policy_number=policy_number, loss_date=loss_date)
    if record_first:
        repository.record(notification)
    outcome = evaluate_notification(notification, policy_client, repository)
    assert outcome.passed is False
    assert outcome.code == expect_code
    if expect_code == "POLICY_NOT_FOUND":
        assert outcome.rule == "V-1"
    if expect_code == "POLICY_CANCELLED":
        assert outcome.rule == "V-7"
        assert outcome.code != "LOSS_AFTER_EXPIRY"


@pytest.mark.parametrize(
    ("policy_number", "loss_date", "expect_recorded"),
    [
        pytest.param(
            "MOT-4479",
            date(2026, 2, 20),
            False,
            id="WI-0142_AC-1_before_inception_not_recorded",
        ),
        pytest.param(
            "MOT-4471",
            date(2026, 4, 2),
            True,
            id="passing_notification_is_recorded",
        ),
    ],
)
def test_submit_notification_records_only_when_every_rule_passes(
    policy_client: StubPolicyClient,
    repository: NotificationRepository,
    policy_number: str,
    loss_date: date,
    expect_recorded: bool,
) -> None:
    notification = make_notification(policy_number=policy_number, loss_date=loss_date)
    result = submit_notification(notification, policy_client, repository)
    found = repository.find_matching(
        notification.policy_number,
        notification.loss_date,
        notification.claim_type,
    )
    if expect_recorded:
        assert isinstance(result, ClaimRecord)
        assert found is not None
        assert found.claim_reference == result.claim_reference
        return
    assert isinstance(result, ValidationOutcome)
    assert found is None
    assert result.passed is False
