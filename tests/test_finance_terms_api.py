"""HTTP behavior, transaction boundaries, and audit recording."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.client import create_finance_terms_client
from app.models import AuditEvent, FinanceTerms
from app.schemas import FinanceTermsCreate


def payload(premium="200.00", tax_fee="50.00") -> dict:
    """Build a valid finance-terms request payload."""
    return {
        "due_date": (datetime.now(UTC).date() + timedelta(days=30)).isoformat(),
        "policies": [
            {
                "insured_name": "Example Business",
                "name": "Commercial Auto",
                "premium": premium,
                "tax_fee": tax_fee,
            }
        ],
    }


def create(client: TestClient, premium="200.00", tax_fee="50.00") -> dict:
    """Create finance terms through the API and return the response body."""
    response = client.post("/finance-terms", json=payload(premium, tax_fee))
    assert response.status_code == 201, response.text
    return response.json()


def history(client, terms_id):
    """Return audit events for a finance-terms record."""
    return client.get(f"/audit?finance_terms_id={terms_id}").json()["data"]


def test_create_terms_computes_amounts_and_audits_policy_snapshot(client: TestClient):
    """Creating terms calculates totals and records a complete policy snapshot."""
    body = payload()
    body["policies"].append(
        {
            "insured_name": "Example Business",
            "name": "General Liability",
            "premium": "300.00",
            "tax_fee": "50.00",
        }
    )
    response = client.post("/finance-terms", json=body)
    assert response.status_code == 201
    terms = response.json()
    assert terms["status"] == "pending"
    assert terms["updated_at"] == terms["created_at"]
    assert terms["agreed_at"] is None
    assert terms["total_amount"] == "600.00"
    assert terms["total_downpayment"] == "200.00"
    assert terms["amount_financed"] == "400.00"
    assert sorted(p["downpayment"] for p in terms["policies"]) == ["110.00", "90.00"]
    events = history(client, terms["id"])
    assert len(events) == 1
    event = events[0]
    assert (event["action"], event["outcome"], event["request_id"]) == (
        "create",
        "succeeded",
        response.headers["X-Request-ID"],
    )
    assert event["details"]["total_downpayment"] == "200.00"
    assert {p["id"] for p in event["details"]["policies"]} == {p["id"] for p in terms["policies"]}


def test_create_terms_rejects_bad_payload_with_standard_validation_errors(
    client: TestClient, db: Session
):
    """Invalid request fields return 422 and leave the database unchanged."""
    body = payload()
    body["due_date"] = "2020-01-01"
    body["policies"][0].update(name="  ", insured_name="", premium="-5", tax_fee="1.234", tax="5")
    response = client.post("/finance-terms", json=body)
    assert response.status_code == 422
    fields = {tuple(item["loc"]) for item in response.json()["detail"]}
    assert response.headers["X-Request-ID"]
    assert {
        ("body", "due_date"),
        ("body", "policies", 0, "name"),
        ("body", "policies", 0, "insured_name"),
        ("body", "policies", 0, "premium"),
        ("body", "policies", 0, "tax_fee"),
        ("body", "policies", 0, "tax"),
    } <= fields
    assert db.scalar(select(func.count()).select_from(FinanceTerms)) == 0
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_terms_due_today_can_be_created_and_agreed(client: TestClient):
    """Terms due today can be created and agreed before the end of that day."""
    body = payload()
    body["due_date"] = datetime.now(UTC).date().isoformat()
    response = client.post("/finance-terms", json=body)
    assert response.status_code == 201
    assert client.post(f"/finance-terms/{response.json()['id']}/agree").status_code == 200


def test_agreement_retry_preserves_timestamp_and_records_outcome(client: TestClient):
    """Repeated agreement requests preserve the timestamp and audit each outcome."""
    terms = create(client)
    url = f"/finance-terms/{terms['id']}/agree"
    first = client.post(url)
    second = client.post(url)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["updated_at"] == first.json()["agreed_at"]
    events = history(client, terms["id"])
    assert [e["outcome"] for e in events] == ["succeeded", "succeeded", "unchanged"]
    assert [e["request_id"] for e in events[1:]] == [
        first.headers["X-Request-ID"],
        second.headers["X-Request-ID"],
    ]
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
    page = client.get(f"/audit?finance_terms_id={terms['id']}&limit=2&offset=1").json()
    assert [e["id"] for e in page["data"]] == [e["id"] for e in events[1:]]
    assert page["has_more"] is False


def test_concurrent_acceptances_record_exactly_one_transition(client: TestClient):
    """Concurrent agreement requests produce one transition and three unchanged events."""
    terms = create(client)
    url = f"/finance-terms/{terms['id']}/agree"
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.post(url), range(4)))
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()["agreed_at"] for r in responses}) == 1
    events = history(client, terms["id"])[1:]
    assert sum(e["outcome"] == "succeeded" for e in events) == 1
    assert sum(e["outcome"] == "unchanged" for e in events) == 3


def test_expired_acceptance_is_audited_without_changing_terms(client: TestClient, db: Session):
    """Expired terms reject agreement, preserve state, and record the rejection."""
    terms = create(client)
    db.execute(
        text("UPDATE finance_terms SET due_date = :due WHERE id = :id"),
        {"due": datetime.now(UTC).date() - timedelta(days=1), "id": terms["id"]},
    )
    db.commit()
    response = client.post(f"/finance-terms/{terms['id']}/agree")
    assert response.status_code == 409
    assert response.json()["error"]["type"] == "invalid_state"
    current = client.get(f"/finance-terms/{terms['id']}").json()
    assert current["status"] == "pending"
    assert current["agreed_at"] is None
    assert current["updated_at"] == terms["updated_at"]
    event = history(client, terms["id"])[-1]
    assert event["outcome"] == "rejected"
    assert event["details"]["reason"] == "terms_expired"


def test_missing_terms_acceptance_is_audited(client: TestClient):
    """Agreement attempts for missing terms return 404 and create an audit event."""
    terms_id = str(uuid.uuid4())
    response = client.post(f"/finance-terms/{terms_id}/agree")
    assert response.status_code == 404
    event = history(client, terms_id)[0]
    assert event["outcome"] == "rejected"
    assert event["details"]["reason"] == "finance_terms_not_found"


def test_list_filters_sorting_and_offset_pagination(client: TestClient):
    """Listing applies filters, sorting, and offset pagination consistently."""
    low = create(client, "100.00", "0")
    mid = create(client, "500.00", "0")
    high = create(client, "1000.00", "0")
    client.post(f"/finance-terms/{mid['id']}/agree")
    response = client.get("/finance-terms", params={"downpayment_gt": 50, "status": "pending"})
    assert [t["id"] for t in response.json()["data"]] == [high["id"]]
    page = client.get("/finance-terms?sort=downpayment&order=asc&limit=2").json()
    assert [t["id"] for t in page["data"]] == [low["id"], mid["id"]]
    assert page["has_more"] is True
    page = client.get("/finance-terms?sort=downpayment&order=asc&limit=2&offset=2").json()
    assert [t["id"] for t in page["data"]] == [high["id"]]
    assert page["has_more"] is False
    response = client.get("/finance-terms?sort=downpayment&order=desc&downpayment_lt=150")
    assert [t["id"] for t in response.json()["data"]] == [mid["id"], low["id"]]
    response = client.get("/finance-terms?downpayment_eq=100")
    assert [t["id"] for t in response.json()["data"]] == [mid["id"]]
    assert client.get("/finance-terms?downpayment_eq=20&downpayment_gt=5").status_code == 422
    assert client.get("/finance-terms?starting_after=old-cursor").status_code == 422


@pytest.mark.parametrize("action", ["create", "agree"])
def test_audit_insert_failure_rolls_back_business_change(
    client: TestClient, db: Session, action: str
):
    """Audit insert failures roll back business changes and sanitize HTTP errors."""
    terms = create(client) if action == "agree" else None
    db.execute(
        text("""
        CREATE FUNCTION reject_audit_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'simulated audit failure'; END; $$;
        CREATE TRIGGER fail_audit_insert BEFORE INSERT ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_insert();
    """)
    )
    db.commit()
    response = (
        client.post(f"/finance-terms/{terms['id']}/agree")
        if terms
        else client.post("/finance-terms", json=payload())
    )
    assert response.status_code == 500
    assert response.json()["error"]["type"] == "internal_error"
    assert "simulated audit failure" not in response.text
    assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]
    if terms:
        assert client.get(f"/finance-terms/{terms['id']}").json() == terms
        assert len(history(client, terms["id"])) == 1
    else:
        assert db.scalar(select(func.count()).select_from(FinanceTerms)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0

    # Direct callers retain the underlying database exception.
    with pytest.raises(DBAPIError, match="simulated audit failure"):
        with create_finance_terms_client("direct-call") as instance:
            if terms:
                instance.agree_finance_terms(uuid.UUID(terms["id"]))
            else:
                instance.create_finance_terms(FinanceTermsCreate.model_validate(payload()))


@pytest.mark.parametrize("fail", [False, True])
def test_client_context_releases_connection_and_rolls_back_uncommitted_changes(
    client: TestClient, session_factory, fail: bool
):
    """The client context rolls back failures and releases its database connection."""
    terms = create(client)
    pool = session_factory.kw["bind"].pool
    expectation = pytest.raises(RuntimeError, match="operation failed") if fail else nullcontext()
    with expectation:
        with create_finance_terms_client("lifecycle-check") as instance:
            row = instance.db.get(FinanceTerms, uuid.UUID(terms["id"]))
            row.due_date += timedelta(days=1)
            instance.db.flush()
            assert pool.checkedout() == 1
            if fail:
                raise RuntimeError("operation failed")
    assert pool.checkedout() == 0
    with create_finance_terms_client("next-operation") as next_instance:
        assert next_instance is not instance
        assert next_instance.db is not instance.db
        assert (
            next_instance.get_finance_terms(uuid.UUID(terms["id"])).due_date.isoformat()
            == terms["due_date"]
        )
    assert pool.checkedout() == 0


def test_audit_lists_all_events_with_optional_terms_filter(client: TestClient):
    """Global audit history paginates across terms; an optional ID narrows the results."""
    assert client.get("/audit").json() == {"data": [], "has_more": False}
    first = create(client)
    second = create(client)
    assert client.post(f"/finance-terms/{first['id']}/agree").status_code == 200

    page = client.get("/audit?limit=2").json()
    assert [event["finance_terms_id"] for event in page["data"]] == [first["id"], second["id"]]
    assert page["has_more"] is True
    next_page = client.get("/audit?limit=2&offset=2").json()
    assert [(event["finance_terms_id"], event["action"]) for event in next_page["data"]] == [
        (first["id"], "agree")
    ]
    assert next_page["has_more"] is False
    events = page["data"] + next_page["data"]
    assert [event["id"] for event in events] == sorted(event["id"] for event in events)

    filtered = client.get("/audit", params={"finance_terms_id": first["id"]}).json()
    assert [event["action"] for event in filtered["data"]] == ["create", "agree"]
    assert all(event["finance_terms_id"] == first["id"] for event in filtered["data"])
    assert filtered["has_more"] is False
    assert client.get("/audit?finance_terms_id=not-a-uuid").status_code == 422
