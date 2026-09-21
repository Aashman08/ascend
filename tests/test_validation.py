"""Contract checks without a database."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.schemas import FinanceTermsCreate, FinanceTermsFilters, PolicyCreate
from app.server import app

PAYOFF = (datetime.now(UTC).date() + timedelta(days=300)).isoformat()


@pytest.mark.parametrize(
    "field,value,message,error_type",
    [
        ("premium", "-1.00", "premium must be a positive value (greater than 0)", "greater_than"),
        ("premium", 0, "premium must be a positive value (greater than 0)", "greater_than"),
        ("tax_fee", "-1.00", "tax_fee must be zero or greater", "greater_than_equal"),
        ("tax_fee", -1, "tax_fee must be zero or greater", "greater_than_equal"),
    ],
)
def test_policy_amount_errors_name_the_field(field, value, message, error_type):
    policy = {"name": "Auto", "insured_name": "Business", "premium": "100", "tax_fee": "0"}
    policy[field] = value
    client = TestClient(app)
    try:
        response = client.post(
            "/finance-terms",
            json={"due_date": datetime.now(UTC).date().isoformat(), "payoff_date": PAYOFF, "policies": [policy]},
        )
        assert response.status_code == 422
        error = response.json()["detail"][0]
        assert error["loc"] == ["body", "policies", 0, field]
        assert error["msg"] == message
        assert error["type"] == error_type
    finally:
        client.close()


@pytest.mark.parametrize("field", ["downpayment_gt", "downpayment_lt", "downpayment_eq"])
def test_negative_downpayment_filter_names_the_field(field):
    client = TestClient(app)
    try:
        response = client.get("/finance-terms", params={field: "-1"})
        assert response.status_code == 422
        error = response.json()["detail"][0]
        assert error["loc"] == ["query", field]
        assert error["msg"] == f"{field} must be zero or greater"
        assert getattr(FinanceTermsFilters(**{field: "0"}), field) == 0
    finally:
        client.close()


@pytest.mark.parametrize("field", ["premium", "tax_fee"])
@pytest.mark.parametrize(
    "value,error_type",
    [
        ("abc", "decimal_parsing"),
        ("NaN", "finite_number"),
        ("1.234", "decimal_max_places"),
        ("10000000000", "less_than_equal"),
    ],
)
def test_other_amount_constraints_are_preserved(field, value, error_type):
    policy = {"name": "Auto", "insured_name": "Business", "premium": "100", "tax_fee": "0"}
    policy[field] = value
    with pytest.raises(ValidationError) as caught:
        PolicyCreate(**policy)
    assert caught.value.errors()[0]["type"] == error_type


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
                "payoff_date": PAYOFF,
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
        payoff_date=PAYOFF,
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


def test_due_date_accepts_dates_but_rejects_numeric_timestamps():
    """The API contract accepts Python dates but rejects numeric date input."""
    policies = [{"name": "Auto", "insured_name": "Business", "premium": "100", "tax_fee": "0"}]
    future = date.today() + timedelta(days=1)

    assert FinanceTermsCreate(due_date=future, payoff_date=PAYOFF, policies=policies).due_date == future
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        FinanceTermsCreate(due_date=1893456000, payoff_date=PAYOFF, policies=policies)


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
        FinanceTermsCreate(due_date=datetime.now(UTC).date(), payoff_date=PAYOFF, policies=[policy, policy])


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


def test_openapi_documents_agreement_error_responses():
    """The agreement endpoint documents its application error responses."""
    responses = app.openapi()["paths"]["/finance-terms/{terms_id}/agree"]["post"]["responses"]

    assert {"200", "404", "409", "422", "500"} <= responses.keys()
    assert responses["404"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ErrorResponse"
    )
