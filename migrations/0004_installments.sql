-- Repayment ledger: one row per scheduled payment (down payment included) for
-- each finance-terms agreement. Rows are created when terms are agreed and
-- updated as payments are recorded.

-- Last day of repayment, distinct from due_date (last day the terms may be
-- agreed). Nullable so terms created before this migration keep working; they
-- simply have no schedule.
ALTER TABLE finance_terms ADD COLUMN payoff_date DATE;

CREATE TYPE installment_status AS ENUM ('pending', 'paid', 'overdue', 'cancelled');
CREATE TYPE installment_type AS ENUM ('downpayment', 'installment');

CREATE TABLE installments (
    id UUID PRIMARY KEY,
    finance_terms_id UUID NOT NULL REFERENCES finance_terms (id) ON DELETE CASCADE,
    installment_id BIGINT NOT NULL,
    installment_type installment_type NOT NULL DEFAULT 'installment',
    installment_value NUMERIC(12, 2) NOT NULL,
    interest_value NUMERIC(12, 2) NOT NULL,
    due_date DATE NOT NULL,
    status installment_status NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at TIMESTAMPTZ
);

CREATE INDEX ix_installments_terms_id ON installments (finance_terms_id, installment_id);
CREATE INDEX ix_installments_finance_terms_id ON installments (finance_terms_id);
CREATE INDEX ix_installments_installment_id ON installments (installment_id);
CREATE INDEX ix_installments_due_date ON installments (due_date);
