"""Transactional audit event recording."""

import uuid

from sqlalchemy.orm import Session

from app.models import AuditAction, AuditEvent, AuditOutcome, FinanceTerms
from app.pricing import DOWNPAYMENT_RATE, TermsTotals

def record_event(
    db: Session,
    terms_id: uuid.UUID,
    action: AuditAction,
    outcome: AuditOutcome,
    request_id: str | None,
    details: dict,
) -> None:
    """The caller owns the transaction; an event never commits independently."""
    db.add(
        AuditEvent(
            finance_terms_id=terms_id,
            action=AuditAction(action).value,
            outcome=AuditOutcome(outcome).value,
            request_id=request_id,
            details=details,
        )
    )


def record_create_event(
    db: Session, terms: FinanceTerms, totals: TermsTotals, request_id: str
) -> None:
    """Record the complete snapshot created for a new finance-terms record."""
    record_event(
        db,
        terms.id,
        AuditAction.create,
        AuditOutcome.succeeded,
        request_id,
        {
            "status": "pending",
            "due_date": terms.due_date.isoformat(),
            "downpayment_rate": str(DOWNPAYMENT_RATE),
            "total_downpayment": str(totals.total_downpayment),
            "total_amount": str(totals.total_amount),
            "amount_financed": str(totals.amount_financed),
            "policies": [
                {
                    "id": str(policy.id),
                    "name": policy.name,
                    "insured_name": policy.insured_name,
                    "premium": str(policy.premium),
                    "tax_fee": str(policy.tax_fee),
                    "downpayment": str(policy.downpayment),
                }
                for policy in terms.policies
            ],
        },
    )


def record_agree_event(
    db: Session,
    terms_id: uuid.UUID,
    request_id: str,
    outcome: AuditOutcome,
    details: dict,
) -> None:
    """Record an agreement attempt while keeping its outcome explicit."""
    record_event(db, terms_id, AuditAction.agree, outcome, request_id, details)


def record_create_replay_event(
    db: Session, terms_id: uuid.UUID, idempotency_key: str, request_id: str
) -> None:
    """Record that a repeated Idempotency-Key returned the existing terms unchanged."""
    record_event(
        db,
        terms_id,
        AuditAction.create,
        AuditOutcome.unchanged,
        request_id,
        {"reason": "idempotent_replay", "idempotency_key": idempotency_key},
    )


def record_cancel_event(
    db: Session,
    terms_id: uuid.UUID,
    request_id: str,
    outcome: AuditOutcome,
    details: dict,
) -> None:
    """Record a cancellation attempt while keeping its outcome explicit."""
    record_event(db, terms_id, AuditAction.cancel, outcome, request_id, details)


def record_payment_event(
    db: Session,
    terms_id: uuid.UUID,
    request_id: str,
    outcome: AuditOutcome,
    details: dict,
) -> None:
    """Record a payment attempt against an installment, approved or not."""
    record_event(db, terms_id, AuditAction.payment, outcome, request_id, details)
