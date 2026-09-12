"""Contract checks without a database."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.schemas import FinanceTermsCreate, PolicyCreate
from app.server import app


@pytest.mark.parametrize("name", [None, "", "   "])
def test_policy_name_must_be_nonblank(name):
    """Policy names reject null, empty, and whitespace-only values."""
    with pytest.raises(ValidationError):
        PolicyCreate(name=name, insured_name="Business", premium="100", tax_fee="0")


def test_policy_name_is_required():
    """A policy must include a name."""
    with pytest.raises(ValidationError):
        PolicyCreate(insured_name="Business", premium="100", tax_fee="0")


def test_policy_downpayment_overflow_is_rejected():
    """A calculated policy downpayment cannot exceed the storage limit."""
    with pytest.raises(ValidationError, match="Policy downpayment"):
        PolicyCreate(
            name="Auto", insured_name="Business", premium="9999999999.99", tax_fee="9999999999.99"
        )


def test_aggregate_downpayment_overflow_is_rejected():
    """The combined downpayment across policies cannot exceed the storage limit."""
    policy = {
        "name": "Auto",
        "insured_name": "Business",
        "premium": "100.00",
        "tax_fee": "5000000000.00",
    }
    with pytest.raises(ValidationError, match="Total downpayment"):
        FinanceTermsCreate(due_date=datetime.now(UTC).date(), policies=[policy, policy])


@pytest.mark.parametrize(
    "method,path,code", [("get", "/missing", 404), ("put", "/finance-terms", 405)]
)
def test_framework_http_errors_keep_standard_response_and_request_id(method, path, code):
    """Framework errors retain their standard body and receive a server request ID."""
    client = TestClient(app)
    response = getattr(client, method)(path)
    assert response.status_code == code
    assert response.json() == {"detail": "Not Found" if code == 404 else "Method Not Allowed"}
    request_id = response.headers["X-Request-ID"]
    UUID(request_id)
    if code == 405:
        assert "allow" in response.headers
    client.close()
