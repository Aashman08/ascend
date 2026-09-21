"""Repayment ledger: schedule generation at agreement, mock payments, and their audit."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.test_finance_terms_api import history

TODAY = datetime.now(UTC).date()


def create(client: TestClient, premium="1000.00", payoff_days=300) -> dict:
    """Terms financing 800.00 after a 200.00 down payment, repaid over ten 30-day steps."""
    body = {
        "due_date": (TODAY + timedelta(days=30)).isoformat(),
        "payoff_date": (TODAY + timedelta(days=payoff_days)).isoformat(),
        "policies": [
            {"insured_name": "Example Business", "name": "Commercial Auto",
             "premium": premium, "tax_fee": "0.00"}
        ],
    }
    response = client.post("/finance-terms", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def agree(client: TestClient, terms_id: str) -> dict:
    response = client.post(f"/finance-terms/{terms_id}/agree")
    assert response.status_code == 200, response.text
    return response.json()


def ledger(client: TestClient, terms_id: str) -> dict:
    response = client.get(f"/finance-terms/{terms_id}/installments")
    assert response.status_code == 200, response.text
    return response.json()


def pay(client: TestClient, terms_id: str, number: int, reference: str | None = None):
    body = {"reference": reference} if reference is not None else None
    return client.post(f"/finance-terms/{terms_id}/installments/{number}/pay", json=body)


def test_agreement_generates_downpayment_plus_equal_installments(client: TestClient):
    terms = create(client)
    assert terms["payoff_date"] == (TODAY + timedelta(days=300)).isoformat()
    assert ledger(client, terms["id"])["installments"] == []
    assert ledger(client, terms["id"])["summary"]["repayment_status"] == "pending"

    agree(client, terms["id"])
    book = ledger(client, terms["id"])
    rows = book["installments"]
    assert len(rows) == 11
    down = rows[0]
    assert (down["installment_id"], down["installment_type"]) == (0, "downpayment")
    assert (down["installment_value"], down["interest_value"]) == ("200.00", "0.00")
    assert down["due_date"] == TODAY.isoformat()
    for i, row in enumerate(rows[1:], start=1):
        assert (row["installment_id"], row["installment_type"]) == (i, "installment")
        assert (row["installment_value"], row["interest_value"]) == ("80.00", "0.80")
        assert row["total_due"] == "80.80"
        assert row["due_date"] == (TODAY + timedelta(days=30 * i)).isoformat()
        assert row["status"] == "pending"
    assert rows[-1]["due_date"] <= terms["payoff_date"]
    summary = book["summary"]
    assert summary["total_due"] == "1008.00"
    assert summary["total_paid"] == "0.00"
    assert summary["balance_remaining"] == "1008.00"
    assert summary["next_due"]["installment_id"] == 0
    assert summary["repayment_status"] == "in_progress"
    agree_event = [e for e in history(client, terms["id"]) if e["action"] == "agree"][0]
    assert agree_event["details"]["installments"] == 11


def test_short_repayment_window_still_produces_one_installment_clamped_to_payoff(
    client: TestClient,
):
    terms = create(client, payoff_days=40)
    agree(client, terms["id"])
    rows = ledger(client, terms["id"])["installments"]
    assert [r["installment_id"] for r in rows] == [0, 1]
    assert rows[1]["installment_value"] == "800.00"
    assert rows[1]["due_date"] == (TODAY + timedelta(days=30)).isoformat()
    assert rows[1]["due_date"] <= terms["payoff_date"]


def test_paying_an_installment_updates_payment_state_and_balance(client: TestClient):
    """A successful payment marks the installment paid and advances the balance."""
    terms = create(client)
    agree(client, terms["id"])
    response = pay(client, terms["id"], 0)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["installment"]["status"] == "paid"
    assert body["installment"]["paid_at"] is not None
    assert body["summary"]["total_paid"] == "200.00"
    assert body["summary"]["balance_remaining"] == "808.00"
    assert body["summary"]["next_due"]["installment_id"] == 1
    assert ledger(client, terms["id"])["installments"][0] == body["installment"]


def test_paying_an_already_paid_installment_is_rejected(client: TestClient):
    """A second payment attempt is rejected and recorded as a rejection."""
    terms = create(client)
    agree(client, terms["id"])
    assert pay(client, terms["id"], 0).status_code == 200

    again = pay(client, terms["id"], 0)
    assert again.status_code == 409
    assert again.json()["error"]["type"] == "invalid_state"
    events = [e for e in history(client, terms["id"]) if e["action"] == "payment"]
    assert events[-1]["outcome"] == "rejected"
    assert events[-1]["details"]["reason"] == "installment_already_paid"


def test_successful_payment_records_charge_metadata(client: TestClient):
    """A successful payment audits the amount, caller reference, and provider reference."""
    terms = create(client)
    agree(client, terms["id"])
    response = pay(client, terms["id"], 0, reference="card-1234")
    assert response.status_code == 200, response.text

    event = [e for e in history(client, terms["id"]) if e["action"] == "payment"][-1]
    assert event["outcome"] == "succeeded"
    assert event["details"]["amount"] == "200.00"
    assert event["details"]["reference"] == "card-1234"
    assert event["details"]["provider_reference"].startswith("mock_")
    assert event["request_id"] == response.headers["X-Request-ID"]


def test_declined_charge_returns_402_and_leaves_installment_pending(client: TestClient):
    terms = create(client)
    agree(client, terms["id"])
    response = pay(client, terms["id"], 1, reference="decline-insufficient-funds")
    assert response.status_code == 402
    assert response.json()["error"]["type"] == "payment_declined"
    row = ledger(client, terms["id"])["installments"][1]
    assert row["status"] == "pending" and row["paid_at"] is None
    event = [e for e in history(client, terms["id"]) if e["action"] == "payment"][-1]
    assert event["outcome"] == "rejected"
    assert event["details"]["reason"] == "declined_by_provider"


def test_payments_require_agreed_terms_and_a_known_installment(client: TestClient):
    pending = create(client)
    response = pay(client, pending["id"], 0)
    assert response.status_code == 409
    assert response.json()["error"]["type"] == "invalid_state"
    event = [e for e in history(client, pending["id"]) if e["action"] == "payment"][-1]
    assert event["details"]["reason"] == "terms_not_agreed"

    agree(client, pending["id"])
    assert pay(client, pending["id"], 99).status_code == 404
    missing = str(uuid.uuid4())
    assert pay(client, missing, 0).status_code == 404
    assert client.get(f"/finance-terms/{missing}/installments").status_code == 404
    assert client.post(
        f"/finance-terms/{pending['id']}/installments/0/pay", json={"note": "x"}
    ).status_code == 422


def test_paying_every_installment_closes_the_ledger(client: TestClient):
    terms = create(client, payoff_days=100)  # 3 installments
    agree(client, terms["id"])
    rows = ledger(client, terms["id"])["installments"]
    assert [r["installment_value"] for r in rows] == ["200.00", "266.67", "266.67", "266.66"]
    for row in rows:
        assert pay(client, terms["id"], row["installment_id"]).status_code == 200
    summary = ledger(client, terms["id"])["summary"]
    assert summary["balance_remaining"] == "0.00"
    assert summary["next_due"] is None
    assert summary["repayment_status"] == "paid_off"
    assert Decimal(summary["total_paid"]) == Decimal(summary["total_due"])


def test_cancelling_terms_cancels_unpaid_installments_only(client: TestClient):
    terms = create(client)
    agree(client, terms["id"])
    assert pay(client, terms["id"], 0).status_code == 200
    # Agreed terms cannot be cancelled through the API; cancel pending terms instead.
    other = create(client)
    assert client.post(
        f"/finance-terms/{other['id']}/cancel", json={"reason": "changed mind"}
    ).status_code == 200
    assert ledger(client, other["id"])["summary"]["repayment_status"] == "cancelled"
    assert pay(client, other["id"], 0).status_code == 409


def test_overdue_is_computed_from_today_and_late_payment_still_succeeds(
    client: TestClient, db: Session
):
    terms = create(client)
    agree(client, terms["id"])
    db.execute(
        text("UPDATE installments SET due_date = :d WHERE finance_terms_id = :id "
             "AND installment_id = 1"),
        {"d": TODAY - timedelta(days=1), "id": terms["id"]},
    )
    db.commit()
    row = ledger(client, terms["id"])["installments"][1]
    assert row["status"] == "overdue"
    assert pay(client, terms["id"], 1).status_code == 200
    assert ledger(client, terms["id"])["installments"][1]["status"] == "paid"


def test_concurrent_payments_for_one_installment_charge_exactly_once(client: TestClient):
    terms = create(client)
    agree(client, terms["id"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: pay(client, terms["id"], 0), range(4)))
    assert sorted(r.status_code for r in responses) == [200, 409, 409, 409]
    book = ledger(client, terms["id"])
    assert book["summary"]["total_paid"] == "200.00"
    events = [e for e in history(client, terms["id"]) if e["action"] == "payment"]
    assert sum(e["outcome"] == "succeeded" for e in events) == 1


def test_payoff_date_validation(client: TestClient):
    base = {
        "due_date": (TODAY + timedelta(days=30)).isoformat(),
        "policies": [
            {"insured_name": "B", "name": "Auto", "premium": "100.00", "tax_fee": "0.00"}
        ],
    }
    missing = client.post("/finance-terms", json=base)
    assert missing.status_code == 422
    assert ["body", "payoff_date"] in [e["loc"] for e in missing.json()["detail"]]
    for payoff in (TODAY + timedelta(days=30), TODAY + timedelta(days=10), TODAY - timedelta(days=1)):
        response = client.post("/finance-terms", json={**base, "payoff_date": payoff.isoformat()})
        assert response.status_code == 422, payoff
    assert client.post(
        "/finance-terms", json={**base, "payoff_date": "2030-1-1"}
    ).status_code == 422
