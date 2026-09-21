import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Money = Numeric(12, 2)


class Base(DeclarativeBase):
    pass


class TermsStatus(str, enum.Enum):
    pending = "pending"
    agreed = "agreed"
    cancelled = "cancelled"


class AuditAction(str, enum.Enum):
    create = "create"
    agree = "agree"
    cancel = "cancel"
    payment = "payment"


class AuditOutcome(str, enum.Enum):
    succeeded = "succeeded"
    unchanged = "unchanged"
    rejected = "rejected"


class InstallmentStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    overdue = "overdue"
    cancelled = "cancelled"


class InstallmentType(str, enum.Enum):
    downpayment = "downpayment"
    installment = "installment"


class FinanceTerms(Base):
    __tablename__ = "finance_terms"
    __table_args__ = (
        CheckConstraint("total_downpayment >= 0", name="terms_downpayment_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    status: Mapped[TermsStatus] = mapped_column(
        Enum(TermsStatus, name="terms_status"),
        default=TermsStatus.pending,
        server_default="pending",
        nullable=False,
    )
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Last day of repayment; distinct from due_date, the last day terms may be agreed.
    payoff_date: Mapped[date | None] = mapped_column(Date)
    total_downpayment: Mapped[Decimal] = mapped_column(Money, nullable=False, index=True)
    agreed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    policies: Mapped[list["Policy"]] = relationship(
        back_populates="finance_terms",
        cascade="all, delete-orphan",
        order_by="(Policy.created_at, Policy.id)",
    )
    installments: Mapped[list["Installment"]] = relationship(
        back_populates="finance_terms",
        cascade="all, delete-orphan",
        order_by="Installment.installment_id",
    )


class Policy(Base):
    __tablename__ = "policies"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="policy_name_nonblank"),
        CheckConstraint("length(trim(insured_name)) > 0", name="policy_insured_name_nonblank"),
        CheckConstraint(
            "premium > 0 AND tax_fee >= 0 AND downpayment >= 0", name="policy_amounts_valid"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    finance_terms_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finance_terms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    insured_name: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    premium: Mapped[Decimal] = mapped_column(Money, nullable=False)
    tax_fee: Mapped[Decimal] = mapped_column(Money, nullable=False)
    downpayment: Mapped[Decimal] = mapped_column(Money, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finance_terms: Mapped[FinanceTerms] = relationship(back_populates="policies")


class AuditEvent(Base):
    """Append-only business history; also records attempts against nonexistent IDs."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_terms_id", "finance_terms_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # No foreign key: rejected attempts can target missing terms; history survives deletion.
    finance_terms_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    # Allowed values are the AuditAction/AuditOutcome enums, enforced in app/audit.py.
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    request_id: Mapped[str | None] = mapped_column(String(200))
    details: Mapped[dict] = mapped_column(JSONB, nullable=False)


class IdempotencyKey(Base):
    """Maps a client-supplied Idempotency-Key to the finance terms it created."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    finance_terms_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finance_terms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

class Installment(Base):
    """One scheduled payment in an agreement's repayment ledger, down payment included."""

    __tablename__ = "installments"
    __table_args__ = (Index("ix_installments_terms_id", "finance_terms_id", "installment_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    finance_terms_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finance_terms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Position within the agreement: 0 is the down payment, 1..N the installments.
    installment_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    installment_type: Mapped[InstallmentType] = mapped_column(
        Enum(InstallmentType, name="installment_type"),
        default=InstallmentType.installment,
        server_default="installment",
        nullable=False,
    )
    installment_value: Mapped[Decimal] = mapped_column(Money, nullable=False)
    interest_value: Mapped[Decimal] = mapped_column(Money, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[InstallmentStatus] = mapped_column(
        Enum(InstallmentStatus, name="installment_status"),
        default=InstallmentStatus.pending,
        server_default="pending",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finance_terms: Mapped[FinanceTerms] = relationship(back_populates="installments")
