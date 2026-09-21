"""Finance terms operations, persistence, and transactional audit recording."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.audit import (
    record_agree_event,
    record_cancel_event,
    record_create_event,
    record_create_replay_event,
    record_event,
)
from app.database import SessionLocal
from app.errors import (
    FinanceTermsNotFoundError,
    IdempotencyConflictError,
    TermsAlreadyAgreedError,
    TermsCancelledError,
    TermsExpiredError,
)
from app.models import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
    FinanceTerms,
    IdempotencyKey,
    Policy,
    TermsStatus,
)
from app.pricing import compute_totals, policy_downpayment
from app.schemas import (
    AuditListResponse,
    CancelRequest,
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

    def create_finance_terms(
        self, payload: FinanceTermsCreate, idempotency_key: str | None = None
    ) -> "CreationResult":
        """Create terms; with an Idempotency-Key, a repeated request returns the original."""
        fingerprint = payload.fingerprint()
        if idempotency_key is not None:
            existing = self._replay_idempotent_create(idempotency_key, fingerprint)
            if existing is not None:
                return CreationResult(existing, replayed=True)

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
        if idempotency_key is not None:
            # The primary key on idempotency_keys is the arbiter under concurrency: a
            # second request with the same new key blocks here until the first commits,
            # then fails with a unique violation and falls back to replaying the winner.
            self.db.add(
                IdempotencyKey(
                    key=idempotency_key, request_hash=fingerprint, finance_terms_id=terms.id
                )
            )
        record_create_event(self.db, terms, totals, self.request_id)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            if (
                idempotency_key is not None
                and _is_idempotency_key_conflict(exc)
            ):
                existing = self._replay_idempotent_create(idempotency_key, fingerprint)
                if existing is None:  # The winner vanished; nothing sensible to replay.
                    raise
                return CreationResult(existing, replayed=True)
            raise
        return CreationResult(finance_terms_response(terms), replayed=False)

    def _replay_idempotent_create(
        self, idempotency_key: str, fingerprint: str
    ) -> FinanceTermsResponse | None:
        """Return the terms originally created with this key, or None if the key is new."""
        stored = self.db.get(IdempotencyKey, idempotency_key)
        if stored is None:
            return None
        if stored.request_hash != fingerprint:
            record_event(
                self.db,
                stored.finance_terms_id,
                AuditAction.create,
                AuditOutcome.rejected,
                self.request_id,
                {"reason": "idempotency_key_reused", "idempotency_key": idempotency_key},
            )
            self.db.commit()
            raise IdempotencyConflictError(idempotency_key)
        record_create_replay_event(
            self.db, stored.finance_terms_id, idempotency_key, self.request_id
        )
        self.db.commit()
        return self.get_finance_terms(stored.finance_terms_id)

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
                AuditOutcome.rejected,
                {"reason": "finance_terms_not_found"},
            )
            self.db.commit()
            raise FinanceTermsNotFoundError(terms_id)
        if terms.status == TermsStatus.cancelled:
            record_agree_event(
                self.db,
                terms_id,
                self.request_id,
                AuditOutcome.rejected,
                {
                    "status": "cancelled",
                    "reason": "terms_cancelled",
                    "cancelled_at": terms.cancelled_at.isoformat(),
                },
            )
            self.db.commit()
            raise TermsCancelledError()
        if terms.status == TermsStatus.agreed:
            record_agree_event(
                self.db,
                terms_id,
                self.request_id,
                AuditOutcome.unchanged,
                {"status": "agreed", "agreed_at": terms.agreed_at.isoformat()},
            )
        elif terms.due_date < datetime.now(UTC).date():
            record_agree_event(
                self.db,
                terms_id,
                self.request_id,
                AuditOutcome.rejected,
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
                AuditOutcome.succeeded,
                {
                    "from_status": "pending",
                    "to_status": "agreed",
                    "agreed_at": terms.agreed_at.isoformat(),
                },
            )
        check_state_consistency(terms)
        self.db.commit()
        return finance_terms_response(terms)

    def cancel_finance_terms(
        self, terms_id: uuid.UUID, payload: CancelRequest
    ) -> FinanceTermsResponse:
        # Same row lock as agreement, so agree and cancel serialize against each other.
        terms = self.db.scalar(
            select(FinanceTerms).where(FinanceTerms.id == terms_id).with_for_update()
        )
        if terms is None:
            record_cancel_event(
                self.db,
                terms_id,
                self.request_id,
                AuditOutcome.rejected,
                {"reason": "finance_terms_not_found"},
            )
            self.db.commit()
            raise FinanceTermsNotFoundError(terms_id)
        if terms.status == TermsStatus.cancelled:
            record_cancel_event(
                self.db,
                terms_id,
                self.request_id,
                AuditOutcome.unchanged,
                {
                    "status": "cancelled",
                    "cancelled_at": terms.cancelled_at.isoformat(),
                    "cancel_reason": terms.cancel_reason,
                },
            )
        elif terms.status == TermsStatus.agreed:
            record_cancel_event(
                self.db,
                terms_id,
                self.request_id,
                AuditOutcome.rejected,
                {
                    "status": "agreed",
                    "reason": "terms_already_agreed",
                    "agreed_at": terms.agreed_at.isoformat(),
                },
            )
            self.db.commit()
            raise TermsAlreadyAgreedError()
        else:
            # Expired pending terms may still be cancelled; expiry only blocks agreement.
            terms.status = TermsStatus.cancelled
            terms.cancel_reason = payload.reason
            terms.cancelled_at = terms.updated_at = datetime.now(UTC)
            record_cancel_event(
                self.db,
                terms_id,
                self.request_id,
                AuditOutcome.succeeded,
                {
                    "from_status": "pending",
                    "to_status": "cancelled",
                    "cancelled_at": terms.cancelled_at.isoformat(),
                    "reason": payload.reason,
                },
            )
        check_state_consistency(terms)
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


def check_state_consistency(terms: FinanceTerms) -> None:
    """Application-owned invariant: each status implies exactly which fields are set.

    This replaces a database check constraint. It runs before every state-changing
    commit so a coding error cannot persist a contradictory row.
    """
    expected = {
        TermsStatus.pending: (False, False, False),
        TermsStatus.agreed: (True, False, False),
        TermsStatus.cancelled: (False, True, True),
    }[terms.status]
    actual = (
        terms.agreed_at is not None,
        terms.cancelled_at is not None,
        terms.cancel_reason is not None,
    )
    if actual != expected:
        raise RuntimeError(
            f"Inconsistent finance terms state for {terms.id}: status={terms.status.value}, "
            f"agreed_at set={actual[0]}, cancelled_at set={actual[1]}, reason set={actual[2]}"
        )


@dataclass(frozen=True)
class CreationResult:
    """Outcome of a create request; replayed is True when an Idempotency-Key matched."""

    terms: FinanceTermsResponse
    replayed: bool


def _is_idempotency_key_conflict(exc: IntegrityError) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == "idempotency_keys_pkey"


@contextmanager
def create_finance_terms_client(request_id: str) -> Iterator[FinanceTermsClient]:
    """Create a client for one operation and always release its database session."""
    with SessionLocal() as db:
        try:
            yield FinanceTermsClient(db, request_id)
        except Exception:
            db.rollback()
            raise
