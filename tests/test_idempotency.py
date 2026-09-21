"""Idempotency-Key behaviour for POST /finance-terms."""

import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import FinanceTerms, IdempotencyKey
from tests.test_finance_terms_api import history, payload


def count(db: Session, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def post(client: TestClient, key: str | None, body: dict | None = None):
    headers = {"Idempotency-Key": key} if key is not None else {}
    return client.post("/finance-terms", json=body or payload(), headers=headers)


def test_repeated_key_returns_original_terms_and_audits_replay(client: TestClient, db: Session):
    """The same key and body twice returns the same record, flagged as a replay."""
    key = str(uuid.uuid4())
    first = post(client, key)
    second = post(client, key)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert "Idempotent-Replayed" not in first.headers
    assert second.headers["Idempotent-Replayed"] == "true"
    assert count(db, FinanceTerms) == 1
    assert count(db, IdempotencyKey) == 1
    events = history(client, first.json()["id"])
    assert [(e["action"], e["outcome"]) for e in events] == [
        ("create", "succeeded"),
        ("create", "unchanged"),
    ]
    assert events[1]["details"]["idempotency_key"] == key
    assert events[1]["request_id"] == second.headers["X-Request-ID"]


def test_key_reused_with_different_body_is_rejected(client: TestClient, db: Session):
    """Reusing a key with a different body returns 409 and creates nothing."""
    key = str(uuid.uuid4())
    original = post(client, key)
    assert original.status_code == 201
    response = post(client, key, payload(premium="999.00"))
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["type"] == "idempotency_conflict"
    assert key in error["message"]
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert count(db, FinanceTerms) == 1
    rejected = history(client, original.json()["id"])[-1]
    assert (rejected["action"], rejected["outcome"]) == ("create", "rejected")
    assert rejected["details"]["reason"] == "idempotency_key_reused"


def test_equivalent_bodies_with_reordered_keys_share_a_fingerprint(client: TestClient, db: Session):
    """Fingerprints come from the validated payload, so key order and whitespace do not matter."""
    key = str(uuid.uuid4())
    body = payload()
    reordered = {"policies": [dict(reversed(list(body["policies"][0].items())))], "due_date": body["due_date"]}
    reordered["policies"][0]["name"] = "  " + reordered["policies"][0]["name"] + "  "
    assert post(client, key, body).status_code == 201
    assert post(client, key, reordered).status_code == 201
    assert count(db, FinanceTerms) == 1


def test_different_keys_or_no_key_create_separate_terms(client: TestClient, db: Session):
    """Idempotency is keyed on the key alone; identical bodies under different keys are distinct."""
    assert post(client, str(uuid.uuid4())).status_code == 201
    assert post(client, str(uuid.uuid4())).status_code == 201
    assert post(client, None).status_code == 201
    assert post(client, None).status_code == 201
    assert count(db, FinanceTerms) == 4
    assert count(db, IdempotencyKey) == 2


def test_concurrent_requests_with_one_key_create_exactly_one_record(client: TestClient, db: Session):
    """The primary key on idempotency_keys arbitrates concurrent first attempts."""
    key = str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: post(client, key), range(4)))
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    assert sum(r.headers.get("Idempotent-Replayed") == "true" for r in responses) == 3
    assert count(db, FinanceTerms) == 1
    assert count(db, IdempotencyKey) == 1


def test_blank_key_is_a_validation_error(client: TestClient):
    response = client.post("/finance-terms", json=payload(), headers={"Idempotency-Key": ""})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["header", "Idempotency-Key"]
