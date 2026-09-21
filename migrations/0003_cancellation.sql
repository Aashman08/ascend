-- Pending terms can be cancelled with a reason. Cancelled terms cannot be agreed.
ALTER TYPE terms_status ADD VALUE 'cancelled';

ALTER TABLE finance_terms
    ADD COLUMN cancelled_at TIMESTAMPTZ,
    ADD COLUMN cancel_reason VARCHAR(500);

-- Business-rule constraints move to the application. The database keeps only
-- structural guarantees: primary/foreign keys, NOT NULL, non-negative amounts,
-- and non-blank names. Status/timestamp consistency is checked in
-- app/client.py before every commit; audit action and outcome values are
-- typed enums in app/models.py.
ALTER TABLE finance_terms DROP CONSTRAINT terms_agreement_consistent;
ALTER TABLE audit_events DROP CONSTRAINT audit_action_valid;
ALTER TABLE audit_events DROP CONSTRAINT audit_outcome_valid;
