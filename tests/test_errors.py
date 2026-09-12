"""Application error contract tests."""

from uuid import uuid4

import pytest

from app.errors import (
    APIError,
    FinanceTermsNotFoundError,
    InvalidStateError,
    NotFoundError,
    TermsExpiredError,
)


@pytest.mark.parametrize(
    "error, status_code, error_type",
    [
        (APIError(), 500, "internal_error"),
        (NotFoundError(), 404, "not_found"),
        (InvalidStateError(), 409, "invalid_state"),
        (TermsExpiredError(), 409, "invalid_state"),
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
