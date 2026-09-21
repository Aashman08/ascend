"""Repayment ledger: schedule rows for agreed terms and their API representation.

Money math lives in app/pricing.py. This module turns a pricing schedule into
Installment rows and turns rows back into the ledger response.
"""

from datetime import UTC, datetime
from decimal import Decimal

from app.models import FinanceTerms, Installment, InstallmentStatus, InstallmentType, TermsStatus
from app.pricing import amount_financed, build_schedule, total_amount
from app.schemas import InstallmentResponse, LedgerResponse, LedgerSummary


def installment_rows(terms: FinanceTerms) -> list[Installment]:
    """Build the ledger for terms being agreed right now; the caller attaches them."""
    if terms.payoff_date is None or terms.agreed_at is None:
        return []
    total = total_amount((p.premium, p.tax_fee) for p in terms.policies)
    schedule = build_schedule(
        total_downpayment=terms.total_downpayment,
        amount_financed=amount_financed(total, terms.total_downpayment),
        start=terms.agreed_at.date(),
        payoff_date=terms.payoff_date,
    )
    return [
        Installment(
            installment_id=row.number,
            installment_type=InstallmentType(row.kind),
            installment_value=row.principal,
            interest_value=row.interest,
            due_date=row.due_date,
            status=InstallmentStatus.pending,
        )
        for row in schedule
    ]


def effective_status(row: Installment) -> InstallmentStatus:
    """Overdue is a fact about today, not a stored state; nothing has to flip it."""
    if row.status == InstallmentStatus.pending and row.due_date < datetime.now(UTC).date():
        return InstallmentStatus.overdue
    return row.status


def installment_response(row: Installment) -> InstallmentResponse:
    return InstallmentResponse(
        id=row.id,
        installment_id=row.installment_id,
        installment_type=row.installment_type,
        installment_value=row.installment_value,
        interest_value=row.interest_value,
        total_due=row.installment_value + row.interest_value,
        due_date=row.due_date,
        status=effective_status(row),
        paid_at=row.paid_at,
    )


def ledger_summary(terms: FinanceTerms) -> LedgerSummary:
    rows = sorted(terms.installments, key=lambda r: r.installment_id)
    total_due = sum((r.installment_value + r.interest_value for r in rows), Decimal("0.00"))
    total_paid = sum(
        (r.installment_value + r.interest_value for r in rows if r.status == InstallmentStatus.paid),
        Decimal("0.00"),
    )
    unpaid = [r for r in rows if r.status == InstallmentStatus.pending]
    if terms.status == TermsStatus.cancelled:
        repayment_status = "cancelled"
    elif terms.status == TermsStatus.pending or not rows:
        repayment_status = "pending"
    elif unpaid:
        repayment_status = "in_progress"
    else:
        repayment_status = "paid_off"
    return LedgerSummary(
        total_due=total_due,
        total_paid=total_paid,
        balance_remaining=total_due - total_paid,
        next_due=installment_response(unpaid[0]) if unpaid else None,
        repayment_status=repayment_status,
    )


def ledger_response(terms: FinanceTerms) -> LedgerResponse:
    rows = sorted(terms.installments, key=lambda r: r.installment_id)
    return LedgerResponse(
        finance_terms_id=terms.id,
        status=terms.status,
        payoff_date=terms.payoff_date,
        installments=[installment_response(r) for r in rows],
        summary=ledger_summary(terms),
    )
