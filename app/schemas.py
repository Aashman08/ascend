"""Request validation and public response contracts. Money serializes as strings."""

import enum
import hashlib
import json
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app import pricing
from app.models import InstallmentStatus, InstallmentType, TermsStatus

class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    insured_name: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200, examples=["Commercial Auto 2026"])
    premium: Decimal = Field(gt=0, le=pricing.MAX_AMOUNT, decimal_places=2)
    tax_fee: Decimal = Field(ge=0, le=pricing.MAX_AMOUNT, decimal_places=2)

    @field_validator("premium", "tax_fee", mode="wrap")
    @classmethod
    def amount_sign_message(
        cls, value: object, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> Decimal:
        try:
            return handler(value)
        except ValidationError as exc:
            error = exc.errors()[0]
            if error["type"] in {"greater_than", "greater_than_equal"}:
                rule = (
                    "a positive value (greater than 0)"
                    if info.field_name == "premium"
                    else "zero or greater"
                )
                raise PydanticCustomError(
                    error["type"], f"{info.field_name} must be {rule}", error["ctx"]
                ) from exc
            raise

    @model_validator(mode="after")
    def downpayment_fits_storage(self) -> "PolicyCreate":
        pricing.validate_policy_downpayment(self.premium, self.tax_fee)
        return self


class FinanceTermsCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_date: date = Field(
        description="Last day terms may be agreed, inclusive; YYYY-MM-DD, today or later (UTC)."
    )
    payoff_date: date = Field(
        description="Last day of repayment, inclusive; YYYY-MM-DD, after due_date."
    )
    policies: list[PolicyCreate] = Field(min_length=1, max_length=100)

    @field_validator("due_date", "payoff_date", mode="before")
    @classmethod
    def date_format(cls, value: object, info: ValidationInfo) -> object:
        if isinstance(value, (int, float, Decimal)):
            raise ValueError(f"{info.field_name} must be a YYYY-MM-DD string")
        if isinstance(value, str) and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError(f"{info.field_name} must use YYYY-MM-DD, for example 2026-05-12")
        return value

    @field_validator("due_date", "payoff_date")
    @classmethod
    def date_not_past(cls, value: date, info: ValidationInfo) -> date:
        if value < datetime.now(UTC).date():
            raise ValueError(f"{info.field_name} must be today or later (UTC)")
        return value

    @model_validator(mode="after")
    def payoff_after_due(self) -> "FinanceTermsCreate":
        if self.payoff_date <= self.due_date:
            raise ValueError("payoff_date must be after due_date")
        return self

    @model_validator(mode="after")
    def total_downpayment_fits_storage(self) -> "FinanceTermsCreate":
        pricing.validate_total_downpayment((p.premium, p.tax_fee) for p in self.policies)
        return self

    def fingerprint(self) -> str:
        """SHA-256 of the validated payload in canonical JSON, for Idempotency-Key reuse checks."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(
        min_length=1, max_length=500, examples=["Customer chose another carrier"]
    )


class SortField(str, enum.Enum):
    downpayment = "downpayment"
    due_date = "due_date"


class SortOrder(str, enum.Enum):
    asc = "asc"
    desc = "desc"


class FinanceTermsFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    downpayment_gt: Decimal | None = Field(None, ge=0, le=pricing.MAX_AMOUNT, decimal_places=2)
    downpayment_lt: Decimal | None = Field(None, ge=0, le=pricing.MAX_AMOUNT, decimal_places=2)
    downpayment_eq: Decimal | None = Field(None, ge=0, le=pricing.MAX_AMOUNT, decimal_places=2)
    status: TermsStatus | None = None
    sort: SortField = SortField.due_date
    order: SortOrder = SortOrder.asc
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0)

    @field_validator("downpayment_gt", "downpayment_lt", "downpayment_eq", mode="wrap")
    @classmethod
    def downpayment_sign_message(
        cls, value: object, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> Decimal | None:
        try:
            return handler(value)
        except ValidationError as exc:
            error = exc.errors()[0]
            if error["type"] == "greater_than_equal":
                raise PydanticCustomError(
                    error["type"], f"{info.field_name} must be zero or greater", error["ctx"]
                ) from exc
            raise

    @model_validator(mode="after")
    def filters_are_consistent(self) -> "FinanceTermsFilters":
        if self.downpayment_eq is not None and (
            self.downpayment_gt is not None or self.downpayment_lt is not None
        ):
            raise ValueError("downpayment_eq cannot combine with downpayment_gt or downpayment_lt")
        if (
            self.downpayment_gt is not None
            and self.downpayment_lt is not None
            and self.downpayment_gt >= self.downpayment_lt
        ):
            raise ValueError("downpayment_gt must be less than downpayment_lt")
        return self


class PolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    insured_name: str
    name: str
    premium: Decimal
    tax_fee: Decimal
    downpayment: Decimal
    created_at: datetime


class FinanceTermsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: TermsStatus
    due_date: date
    payoff_date: date | None
    total_downpayment: Decimal
    total_amount: Decimal
    amount_financed: Decimal
    agreed_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    created_at: datetime
    updated_at: datetime
    policies: list[PolicyResponse]


class FinanceTermsListResponse(BaseModel):
    data: list[FinanceTermsResponse]
    has_more: bool


class ErrorBody(BaseModel):
    type: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    finance_terms_id: uuid.UUID
    action: str
    outcome: str
    occurred_at: datetime
    request_id: str | None
    details: dict


class AuditListResponse(BaseModel):
    data: list[AuditEventResponse]
    has_more: bool


class InstallmentResponse(BaseModel):
    id: uuid.UUID
    installment_id: int
    installment_type: InstallmentType
    installment_value: Decimal
    interest_value: Decimal
    total_due: Decimal
    due_date: date
    status: InstallmentStatus
    paid_at: datetime | None


class LedgerSummary(BaseModel):
    total_due: Decimal
    total_paid: Decimal
    balance_remaining: Decimal
    next_due: InstallmentResponse | None
    repayment_status: str = Field(examples=["pending", "in_progress", "paid_off", "cancelled"])


class LedgerResponse(BaseModel):
    finance_terms_id: uuid.UUID
    status: TermsStatus
    payoff_date: date | None
    installments: list[InstallmentResponse]
    summary: LedgerSummary


class PaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reference: str | None = Field(
        None,
        min_length=1,
        max_length=200,
        description="Caller's reference for the charge. Mock provider declines references "
        "starting with 'decline'.",
    )


class PaymentResponse(BaseModel):
    installment: InstallmentResponse
    summary: LedgerSummary
