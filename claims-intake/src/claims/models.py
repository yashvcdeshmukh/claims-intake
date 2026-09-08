"""Boundary models for the claims intake service.

Everything that enters the service is parsed into one of these before any rule
runs. A payload that reaches the rule layer has already been proven well formed,
which is what keeps a shape problem and a content problem from arriving at the
caller as the same status code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from claims.policy_client import PolicyRecord

ClaimType = Literal["collision", "theft", "glass", "liability", "weather"]

CLAIM_REFERENCE_PATTERN = r"^CLM-\d{4}-\d{6}$"


def _require_two_decimal_places(value: Decimal) -> Decimal:
    if value.as_tuple().exponent != -2:
        raise ValueError("must have exactly two decimal places")
    return value


class NotificationRequest(BaseModel):
    """A first notice of loss as submitted by the claims portal.

    Fields and their constraints are specified in contract section 2.2. The model
    is responsible for the shape of the request and for nothing else. Whether the
    policy exists, whether the loss falls inside the term, and whether the amount
    is within the limit are rules, and rules live in `service.py`.
    """

    model_config = ConfigDict(extra="forbid")

    policy_number: str = Field(min_length=1)
    loss_date: date
    claim_type: ClaimType
    estimated_amount: Decimal
    description: str | None = None

    @field_validator("estimated_amount")
    @classmethod
    def amount_two_places_and_positive(cls, value: Decimal) -> Decimal:
        value = _require_two_decimal_places(value)
        if value <= 0:
            raise ValueError("estimated_amount must be greater than zero")
        return value


class Policy(BaseModel):
    """A policy as this service works with it.

    Built from the `PolicyRecord` the policy client returns. The fields the rules
    compare against are the reason this model exists.
    """

    model_config = ConfigDict(extra="forbid")

    policy_number: str = Field(min_length=1)
    product: str = Field(min_length=1)
    effective_date: date
    expiry_date: date
    # WI-0158 AC-3: absence is None. A comparison against date | None without a
    # None check is a type error, so skipping this rule is not silent.
    cancellation_date: date | None = None
    limit: Decimal
    permitted_claim_types: tuple[str, ...]

    @field_validator("effective_date", "expiry_date", "cancellation_date", mode="before")
    @classmethod
    def dates_are_date_values(cls, value: object) -> object:
        if value is None:
            return value
        if type(value) is not date:
            raise ValueError("policy dates must be date values, not strings")
        return value

    @field_validator("limit")
    @classmethod
    def limit_has_two_decimal_places(cls, value: Decimal) -> Decimal:
        return _require_two_decimal_places(value)

    @classmethod
    def from_record(cls, record: PolicyRecord) -> Policy:
        return cls(
            policy_number=record.policy_number,
            product=record.product,
            effective_date=record.effective_date,
            expiry_date=record.expiry_date,
            cancellation_date=record.cancellation_date,
            limit=record.limit,
            permitted_claim_types=record.permitted_claim_types,
        )


@dataclass(frozen=True)
class RuleFailure:
    """The two values a rule refusal names.

    `rule` is the identifier (V-2). `code` is the stable contract code
    (LOSS_BEFORE_INCEPTION). Separate fields so one cannot be passed where the
    other is expected.
    """

    rule: str
    code: str


class ClaimRecord(BaseModel):
    """A notification that passed every rule and was written.

    Carries the claim reference issued at the time it was recorded. Contract
    section 3 fixes the reference format.
    """

    model_config = ConfigDict(extra="forbid")

    policy_number: str = Field(min_length=1)
    loss_date: date
    claim_type: ClaimType
    estimated_amount: Decimal
    description: str | None = None
    claim_reference: str = Field(pattern=CLAIM_REFERENCE_PATTERN)

    @field_validator("loss_date", mode="before")
    @classmethod
    def loss_date_is_a_date(cls, value: object) -> object:
        if type(value) is not date:
            raise ValueError("loss_date must be a date value, not a string")
        return value

    @field_validator("estimated_amount")
    @classmethod
    def amount_two_places_and_positive(cls, value: Decimal) -> Decimal:
        value = _require_two_decimal_places(value)
        if value <= 0:
            raise ValueError("estimated_amount must be greater than zero")
        return value


# Shipped name in service.py. Same object; Day 3 imports this.
type RecordedNotification = ClaimRecord
