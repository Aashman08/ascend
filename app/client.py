"""Finance terms operations, persistence, and transactional audit recording."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.audit import record_agree_event, record_create_event
from app.database import SessionLocal
from app.errors import FinanceTermsNotFoundError, TermsExpiredError
from app.models import AuditEvent, FinanceTerms, Policy, TermsStatus
from app.pricing import compute_totals, policy_downpayment
from app.schemas import (
    AuditListResponse,
    FinanceTermsCreate,
    FinanceTermsFilters,
    FinanceTermsListResponse,
    FinanceTermsResponse,
    SortField,
    SortOrder,
)
from app.serializers import finance_terms_response


class FinanceTermsClient:
    """One request's operations; the factory manages the database session lifetime."""

    def __init__(self, db: Session, request_id: str) -> None:
        self.db = db
        self.request_id = request_id

    def create_finance_terms(self, payload: FinanceTermsCreate) -> FinanceTermsResponse:
        totals = compute_totals([(p.premium, p.tax_fee) for p in payload.policies])
        terms = FinanceTerms(
            due_date=payload.due_date,
            status=TermsStatus.pending,
            total_downpayment=totals.total_downpayment,
            policies=[
                Policy(
                    insured_name=p.insured_name,
                    name=p.name,
                    premium=p.premium,
                    tax_fee=p.tax_fee,
                    downpayment=policy_downpayment(p.premium, p.tax_fee),
                )
                for p in payload.policies
            ],
        )
        self.db.add(terms)
        self.db.flush()
        record_create_event(self.db, terms, totals, self.request_id)
        self.db.commit()
        return finance_terms_response(terms)

    def get_finance_terms(self, terms_id: uuid.UUID) -> FinanceTermsResponse:
        terms = self.db.scalar(
            select(FinanceTerms)
            .options(selectinload(FinanceTerms.policies))
            .where(FinanceTerms.id == terms_id)
        )
        if terms is None:
            raise FinanceTermsNotFoundError(terms_id)
        return finance_terms_response(terms)

    def agree_finance_terms(self, terms_id: uuid.UUID) -> FinanceTermsResponse:
        # Serialize acceptance attempts so retries preserve the first agreement timestamp.
        terms = self.db.scalar(
            select(FinanceTerms).where(FinanceTerms.id == terms_id).with_for_update()
        )
        if terms is None:
            record_agree_event(
                self.db,
                terms_id,
                self.request_id,
                "rejected",
                {"reason": "finance_terms_not_found"},
            )
            self.db.commit()
            raise FinanceTermsNotFoundError(terms_id)
        if terms.status == TermsStatus.agreed:
            record_agree_event(
                self.db,
                terms_id,
                self.request_id,
                "unchanged",
                {"status": "agreed", "agreed_at": terms.agreed_at.isoformat()},
            )
        elif terms.due_date < datetime.now(UTC).date():
            record_agree_event(
                self.db,
                terms_id,
                self.request_id,
                "rejected",
                {
                    "status": "pending",
                    "reason": "terms_expired",
                    "due_date": terms.due_date.isoformat(),
                },
            )
            self.db.commit()
            raise TermsExpiredError()
        else:
            terms.status = TermsStatus.agreed
            terms.agreed_at = terms.updated_at = datetime.now(UTC)
            record_agree_event(
                self.db,
                terms_id,
                self.request_id,
                "succeeded",
                {
                    "from_status": "pending",
                    "to_status": "agreed",
                    "agreed_at": terms.agreed_at.isoformat(),
                },
            )
        self.db.commit()
        return finance_terms_response(terms)

    def list_finance_terms(self, filters: FinanceTermsFilters) -> FinanceTermsListResponse:
        query = select(FinanceTerms).options(selectinload(FinanceTerms.policies))
        if filters.downpayment_gt is not None:
            query = query.where(FinanceTerms.total_downpayment > filters.downpayment_gt)
        if filters.downpayment_lt is not None:
            query = query.where(FinanceTerms.total_downpayment < filters.downpayment_lt)
        if filters.downpayment_eq is not None:
            query = query.where(FinanceTerms.total_downpayment == filters.downpayment_eq)
        if filters.status is not None:
            query = query.where(FinanceTerms.status == filters.status)
        sort_column = (
            FinanceTerms.total_downpayment
            if filters.sort == SortField.downpayment
            else FinanceTerms.due_date
        )
        order = (
            (sort_column.desc(), FinanceTerms.id.desc())
            if filters.order == SortOrder.desc
            else (sort_column.asc(), FinanceTerms.id.asc())
        )
        rows = list(
            self.db.scalars(query.order_by(*order).offset(filters.offset).limit(filters.limit + 1))
        )
        return FinanceTermsListResponse(
            data=[finance_terms_response(row) for row in rows[: filters.limit]],
            has_more=len(rows) > filters.limit,
        )

    def list_audit_events(
        self, finance_terms_id: uuid.UUID | None = None, *, limit: int = 20, offset: int = 0
    ) -> AuditListResponse:
        query = select(AuditEvent)
        if finance_terms_id is not None:
            query = query.where(AuditEvent.finance_terms_id == finance_terms_id)
        rows = list(self.db.scalars(query.order_by(AuditEvent.id).offset(offset).limit(limit + 1)))

        return AuditListResponse(data=rows[:limit], has_more=len(rows) > limit)


@contextmanager
def create_finance_terms_client(request_id: str) -> Iterator[FinanceTermsClient]:
    """Create a client for one operation and always release its database session."""
    with SessionLocal() as db:
        try:
            yield FinanceTermsClient(db, request_id)
        except Exception:
            db.rollback()
            raise
