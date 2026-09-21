"""Application error contract tests."""

from uuid import uuid4

import pytest

from app.errors import (
    APIError,
    FinanceTermsNotFoundError,
    IdempotencyConflictError,
    InvalidStateError,
    InstallmentAlreadyCancelledError,
    InstallmentAlreadyPaidError,
    InstallmentNotFoundError,
    NotFoundError,
    PaymentDeclinedError,
    TermsAlreadyAgreedError,
    TermsCancelledError,
    TermsExpiredError,
    TermsNotAgreedError,
)


@pytest.mark.parametrize(
    "error, status_code, error_type",
    [
        (APIError(), 500, "internal_error"),
        (NotFoundError(), 404, "not_found"),
        (InvalidStateError(), 409, "invalid_state"),
        (TermsExpiredError(), 409, "invalid_state"),
        (IdempotencyConflictError("order-1"), 409, "idempotency_conflict"),
        (TermsCancelledError(), 409, "invalid_state"),
        (TermsAlreadyAgreedError(), 409, "invalid_state"),
        (TermsNotAgreedError(), 409, "invalid_state"),
        (InstallmentAlreadyPaidError(), 409, "invalid_state"),
        (InstallmentAlreadyCancelledError(), 409, "invalid_state"),
        (InstallmentNotFoundError(3, uuid4()), 404, "not_found"),
        (PaymentDeclinedError(), 402, "payment_declined"),
    ],
)
def test_application_error_contract(error, status_code, error_type):
    assert (error.status_code, error.error_type) == (status_code, error_type)
    assert str(error) == error.message


def test_missing_terms_error_includes_the_requested_id():
    terms_id = uuid4()

    error = FinanceTermsNotFoundError(terms_id)

    assert error.status_code == 404
    assert error.error_type == "not_found"
    assert str(terms_id) in error.message
