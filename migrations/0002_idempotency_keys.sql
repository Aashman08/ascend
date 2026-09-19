-- Client-supplied Idempotency-Key values for POST /finance-terms.
-- One key maps to exactly one finance-terms record; the primary key is the
-- arbiter under concurrency. request_hash detects reuse of a key with a
-- different payload. Global uniqueness for now; becomes (actor_id, key) once
-- callers are authenticated.
CREATE TABLE idempotency_keys (
    key VARCHAR(200) PRIMARY KEY,
    request_hash VARCHAR(64) NOT NULL,
    finance_terms_id UUID NOT NULL REFERENCES finance_terms (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_idempotency_keys_finance_terms_id ON idempotency_keys (finance_terms_id);
