-- Runs once when the Postgres volume is first created.
-- The image creates the "ascend" database from POSTGRES_DB; this adds an
-- isolated database for the test suite so tests never touch demo data.
CREATE DATABASE ascend_test;
