"""Cancellation of pending terms and its interaction with agreement."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.client import check_state_consistency
from app.models import FinanceTerms, TermsStatus
from tests.test_finance_terms_api import create, history


def cancel(client: TestClient, terms_id: str, reason: str = "Customer chose another carrier"):
    return client.post(f"/finance-terms/{terms_id}/cancel", json={"reason": reason})


def test_cancel_pending_terms_records_reason_and_audit(client: TestClient):
    """Cancelling pending terms sets status, timestamps, reason, and an audit event."""
    terms = create(client)
    response = cancel(client, terms["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["cancel_reason"] == "Customer chose another carrier"
    assert body["cancelled_at"] == body["updated_at"]
    assert body["agreed_at"] is None
    assert client.get(f"/finance-terms/{terms['id']}").json() == body
    event = history(client, terms["id"])[-1]
    assert (event["action"], event["outcome"]) == ("cancel", "succeeded")
    assert event["details"]["reason"] == "Customer chose another carrier"
    assert event["details"]["to_status"] == "cancelled"
    assert event["request_id"] == response.headers["X-Request-ID"]


def test_repeated_cancellation_is_unchanged_and_keeps_original_reason(client: TestClient):
    """A second cancellation returns 200 with the first timestamp and reason."""
    terms = create(client)
    first = cancel(client, terms["id"], "first reason")
    second = cancel(client, terms["id"], "second reason")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert second.json()["cancel_reason"] == "first reason"
    outcomes = [e["outcome"] for e in history(client, terms["id"]) if e["action"] == "cancel"]
    assert outcomes == ["succeeded", "unchanged"]


def test_cancelled_terms_cannot_be_agreed(client: TestClient):
    """Agreeing cancelled terms returns 409 and records a rejection."""
    terms = create(client)
    assert cancel(client, terms["id"]).status_code == 200
    response = client.post(f"/finance-terms/{terms['id']}/agree")
    assert response.status_code == 409
    assert response.json()["error"]["type"] == "invalid_state"
    assert "cancelled" in response.json()["error"]["message"]
    current = client.get(f"/finance-terms/{terms['id']}").json()
    assert current["status"] == "cancelled"
    event = history(client, terms["id"])[-1]
    assert (event["action"], event["outcome"]) == ("agree", "rejected")
    assert event["details"]["reason"] == "terms_cancelled"


def test_agreed_terms_cannot_be_cancelled(client: TestClient):
    """Cancelling agreed terms returns 409, leaves the record unchanged, and audits it."""
    terms = create(client)
    agreed = client.post(f"/finance-terms/{terms['id']}/agree").json()
    response = cancel(client, terms["id"])
    assert response.status_code == 409
    assert response.json()["error"]["type"] == "invalid_state"
    assert client.get(f"/finance-terms/{terms['id']}").json() == agreed
    event = history(client, terms["id"])[-1]
    assert (event["action"], event["outcome"]) == ("cancel", "rejected")
    assert event["details"]["reason"] == "terms_already_agreed"


def test_expired_pending_terms_can_still_be_cancelled(client: TestClient, db: Session):
    """Expiry blocks agreement, not tidy-up: expired pending terms may be cancelled."""
    terms = create(client)
    db.execute(
        text("UPDATE finance_terms SET due_date = :due WHERE id = :id"),
        {"due": datetime.now(UTC).date() - timedelta(days=1), "id": terms["id"]},
    )
    db.commit()
    assert cancel(client, terms["id"]).status_code == 200


def test_cancelling_missing_terms_is_audited(client: TestClient):
    terms_id = str(uuid.uuid4())
    response = cancel(client, terms_id)
    assert response.status_code == 404
    event = history(client, terms_id)[0]
    assert (event["action"], event["outcome"]) == ("cancel", "rejected")
    assert event["details"]["reason"] == "finance_terms_not_found"


def test_cancel_reason_is_required_and_trimmed(client: TestClient):
    terms = create(client)
    for body in ({}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 501}, {"why": "no"}):
        response = client.post(f"/finance-terms/{terms['id']}/cancel", json=body)
        assert response.status_code == 422, body
    response = cancel(client, terms["id"], "  padded  ")
    assert response.json()["cancel_reason"] == "padded"


def test_cancelled_terms_appear_only_under_their_status_filter(client: TestClient):
    pending = create(client)
    cancelled = create(client)
    assert cancel(client, cancelled["id"]).status_code == 200
    ids = lambda status: [t["id"] for t in client.get(f"/finance-terms?status={status}").json()["data"]]
    assert ids("cancelled") == [cancelled["id"]]
    assert ids("pending") == [pending["id"]]


def test_concurrent_agree_and_cancel_allow_exactly_one_transition(client: TestClient):
    """Both actions lock the row, so one wins and the other is rejected."""
    terms = create(client)
    actions = [
        lambda: client.post(f"/finance-terms/{terms['id']}/agree"),
        lambda: cancel(client, terms["id"]),
    ] * 2
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda act: act(), actions))
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 200, 409, 409]
    final = client.get(f"/finance-terms/{terms['id']}").json()
    assert final["status"] in {"agreed", "cancelled"}
    events = [e for e in history(client, terms["id"]) if e["action"] != "create"]
    assert sum(e["outcome"] == "succeeded" for e in events) == 1
    assert sum(e["outcome"] == "unchanged" for e in events) == 1
    assert sum(e["outcome"] == "rejected" for e in events) == 2


def test_state_consistency_check_blocks_contradictory_rows():
    """The application invariant that replaced the database constraint."""
    now = datetime.now(UTC)
    check_state_consistency(FinanceTerms(status=TermsStatus.pending))
    check_state_consistency(FinanceTerms(status=TermsStatus.agreed, agreed_at=now))
    check_state_consistency(
        FinanceTerms(status=TermsStatus.cancelled, cancelled_at=now, cancel_reason="x")
    )
    with pytest.raises(RuntimeError, match="Inconsistent"):
        check_state_consistency(FinanceTerms(status=TermsStatus.agreed))
    with pytest.raises(RuntimeError, match="Inconsistent"):
        check_state_consistency(FinanceTerms(status=TermsStatus.cancelled, cancelled_at=now))
    with pytest.raises(RuntimeError, match="Inconsistent"):
        check_state_consistency(FinanceTerms(status=TermsStatus.pending, agreed_at=now))
