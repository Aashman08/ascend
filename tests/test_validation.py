"""Contract checks without a database."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.schemas import FinanceTermsCreate, PolicyCreate
from app.server import app


@pytest.mark.parametrize(
    "due_date,message",
    [
        ("2026-5-12", "due_date must use YYYY-MM-DD"),
        ("2026-05-2", "due_date must use YYYY-MM-DD"),
        ("2000-05-12", "due_date must be today or later (UTC)"),
    ],
)
def test_due_date_format_and_past_date_have_distinct_errors(due_date, message):
    client = TestClient(app)
    try:
        response = client.post(
            "/finance-terms",
            json={
                "due_date": due_date,
                "policies": [
                    {"name": "Auto", "insured_name": "Business", "premium": "100", "tax_fee": "0"}
                ],
            },
        )
        assert response.status_code == 422
        error = response.json()["detail"][0]
        assert error["loc"] == ["body", "due_date"]
        assert message in error["msg"]
    finally:
        client.close()


def test_due_date_accepts_today_in_iso_format():
    today = datetime.now(UTC).date()
    terms = FinanceTermsCreate(
        due_date=today.isoformat(),
        policies=[{"name": "Auto", "insured_name": "Business", "premium": "100", "tax_fee": "0"}],
    )
    assert terms.due_date == today


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
