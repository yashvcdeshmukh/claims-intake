"""Rule evaluation and notification submission.

This module owns the decision. It does not know it was reached over HTTP, which
is why it can be tested by calling a function with a typed object and asserting on
the result with no server running.

`evaluate_notification` is the decision. It takes a notification and a policy and
returns `RuleFailure | None`. It has no side effects and touches nothing outside
itself: no client, no repository, no write. A `None` means every policy-field
rule passed. A `RuleFailure` names the first one that did not.

`submit_notification` is the orchestration. It resolves the policy through the
client, evaluates, and records only if evaluation passed. Two things the client
can raise are not the same condition and are not handled the same way.
`PolicyNotFound` means the master answered and said no: that is V-1, and it
becomes a `RuleFailure` like any other rule outcome. `PolicyLookupFailed` means
you do not know. That is not a rule outcome. It is not caught here, so it
reaches the HTTP layer intact and tomorrow's routes can map its `reason` to the
status in contract section 6.

Day 3 assignment. Build the remaining rules against `docs/api-contract.md`
section 4.
"""

from __future__ import annotations

from collections.abc import Callable

from claims.models import (
    NotificationRequest,
    Policy,
    RecordedNotification,
    RuleFailure,
)
from claims.policy_client import PolicyClient, PolicyNotFound
from claims.repository import NotificationRepository


def evaluate_loss_after_inception(
    notification: NotificationRequest,
    policy: Policy,
) -> RuleFailure | None:
    """V-2. The loss must not precede policy inception.

    The boundary is stated in contract section 4.2 and in WI-0142 AC-3. A loss on
    the inception date is covered.
    """
    if notification.loss_date >= policy.effective_date:
        return None
    return RuleFailure(rule="V-2", code="LOSS_BEFORE_INCEPTION")


def evaluate_loss_before_expiry(
    notification: NotificationRequest,
    policy: Policy,
) -> RuleFailure | None:
    """V-3. The loss must not fall after the policy expiry date."""
    if notification.loss_date <= policy.expiry_date:
        return None
    return RuleFailure(rule="V-3", code="LOSS_AFTER_EXPIRY")


def evaluate_amount_within_limit(
    notification: NotificationRequest,
    policy: Policy,
) -> RuleFailure | None:
    """V-4. The estimated amount must not exceed the policy limit.

    An amount equal to the limit is within cover, per contract section 4.2.
    """
    if notification.estimated_amount <= policy.limit:
        return None
    return RuleFailure(rule="V-4", code="AMOUNT_EXCEEDS_LIMIT")


def evaluate_claim_type_covered(
    notification: NotificationRequest,
    policy: Policy,
) -> RuleFailure | None:
    """V-5. The claim type must be permitted on the policy's product."""
    if notification.claim_type in policy.permitted_claim_types:
        return None
    return RuleFailure(rule="V-5", code="TYPE_NOT_COVERED")


def evaluate_duplicate_notification(
    notification: NotificationRequest,
    repository: NotificationRepository,
) -> RuleFailure | None:
    """V-6. The notification must not duplicate a recorded loss event.

    This is a query against what has been recorded, not a comparison to a policy
    field, which is why it is not called from `evaluate_notification`.
    """
    existing = repository.find_matching(
        notification.policy_number,
        notification.loss_date,
        notification.claim_type,
    )
    if existing is None:
        return None
    return RuleFailure(rule="V-6", code="DUPLICATE_NOTIFICATION")


def evaluate_loss_before_cancellation(
    notification: NotificationRequest,
    policy: Policy,
) -> RuleFailure | None:
    """V-7. Cover has ended when cancellation_date is set and the loss is on or after it."""
    if policy.cancellation_date is None:
        return None
    if notification.loss_date < policy.cancellation_date:
        return None
    return RuleFailure(rule="V-7", code="POLICY_CANCELLED")


PolicyRule = Callable[[NotificationRequest, Policy], RuleFailure | None]

# Pure functions of (notification, policy), in section 4.1 order among themselves.
# V-1 is not listed: it is PolicyNotFound at the client boundary in submit.
# V-6 is not listed: it is a repository query, also in submit.
POLICY_RULES: tuple[PolicyRule, ...] = (
    evaluate_loss_before_cancellation,  # V-7
    evaluate_loss_after_inception,  # V-2
    evaluate_loss_before_expiry,  # V-3
    evaluate_claim_type_covered,  # V-5
    evaluate_amount_within_limit,  # V-4
)


def evaluate_notification(
    notification: NotificationRequest,
    policy: Policy,
) -> RuleFailure | None:
    """Return the first policy-field rule that fails, or None.

    A notification can violate several rules at once and the caller sees one
    reason, so the order this function evaluates in is a caller-visible behavior.
    It is fixed by contract section 4.1 among the rules that read a policy field:
    V-7, V-2, V-3, V-5, V-4. V-1 and V-6 do not belong here. V-1 is a fact about
    the lookup, and V-6 is a fact about the store.
    """
    for rule in POLICY_RULES:
        failure = rule(notification, policy)
        if failure is not None:
            return failure
    return None


def submit_notification(
    notification: NotificationRequest,
    policy_client: PolicyClient,
    repository: NotificationRepository,
) -> RecordedNotification | RuleFailure:
    """Resolve the policy, evaluate, and record only if every rule passed.

    `PolicyNotFound` is V-1. `PolicyLookupFailed` is not caught: this function
    cannot answer it, and the HTTP layer has to see the `reason`.

    V-6 sits between V-7 and V-2 (section 4.1). It cannot run inside
    `evaluate_notification`, so V-7 is checked here first, then the store, then
    the remaining coverage rules. Running V-7 first also means a cancelled
    policy that is also after expiry reports POLICY_CANCELLED (WI-0158 AC-4).
    """
    try:
        record = policy_client.get_policy(notification.policy_number)
    except PolicyNotFound:
        return RuleFailure(rule="V-1", code="POLICY_NOT_FOUND")

    policy = Policy.from_record(record)

    if failure := evaluate_loss_before_cancellation(notification, policy):
        return failure
    if failure := evaluate_duplicate_notification(notification, repository):
        return failure
    if failure := evaluate_notification(notification, policy):
        return failure
    return repository.record(notification)
