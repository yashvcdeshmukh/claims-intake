"""Persistence for recorded notifications.

An in-memory store is sufficient for Week 1 and is deliberate rather than a
shortcut. The rules do not know where a notification is stored, so replacing this
with a database in a later week is a change to one module.

The duplicate check that `WI-0151` describes is a query against what has been
recorded, which is why it belongs here rather than in the rule table.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from claims.models import ClaimRecord, NotificationRequest


class NotificationRepository:
    """Stores recorded notifications and issues claim references.

    Recording is the only write. A notification that was refused is never passed
    here, so it cannot become a duplicate (WI-0151 AC-3).
    """

    def __init__(self) -> None:
        self._records: list[ClaimRecord] = []
        self._sequence = 0

    def issue_claim_reference(self) -> str:
        """Return the next claim reference and consume it.

        Format is contract section 3: `CLM-YYYY-NNNNNN`. References are unique
        and are never reissued.
        """
        self._sequence += 1
        year = datetime.now(tz=UTC).year
        return f"CLM-{year}-{self._sequence:06d}"

    def record(self, notification: NotificationRequest) -> ClaimRecord:
        """Write a notification and return it with its issued claim reference."""
        recorded = ClaimRecord(
            policy_number=notification.policy_number,
            loss_date=notification.loss_date,
            claim_type=notification.claim_type,
            estimated_amount=notification.estimated_amount,
            description=notification.description,
            claim_reference=self.issue_claim_reference(),
        )
        self._records.append(recorded)
        return recorded

    def find_matching(
        self,
        policy_number: str,
        loss_date: date,
        claim_type: str,
    ) -> ClaimRecord | None:
        """Return an existing recorded notification matching all three values.

        `WI-0151` AC-1 fixes which fields constitute a match. AC-3 is the reason
        this searches recorded notifications only: a submission that was refused
        was never written, so there is nothing for a later one to duplicate.
        """
        for recorded in self._records:
            if (
                recorded.policy_number == policy_number
                and recorded.loss_date == loss_date
                and recorded.claim_type == claim_type
            ):
                return recorded
        return None
