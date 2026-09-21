"""SQL migrations preserve data, apply once, and roll back on failure."""

from concurrent.futures import ThreadPoolExecutor
import shutil
from threading import Barrier
import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError

from app.database import MIGRATIONS_DIR, migrate_database
from app.models import Base

COMMITTED = [script.name for script in sorted(MIGRATIONS_DIR.glob("[0-9]*_*.sql"))]


def test_fresh_database_matches_models_and_restart_preserves_data(database_engine):
    migrate_database(database_engine)
    inspector = inspect(database_engine)
    for table in Base.metadata.sorted_tables:
        columns = inspector.get_columns(table.name)
        assert {c["name"] for c in columns} == set(table.columns.keys())
        for column in columns:
            expected = table.columns[column["name"]]
            assert column["nullable"] == expected.nullable
            assert column["type"].compile(dialect=database_engine.dialect) == (
                expected.type.compile(dialect=database_engine.dialect)
            )
        assert {index["name"] for index in inspector.get_indexes(table.name)} == {
            index.name for index in table.indexes
        }
    terms_id = uuid.uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO finance_terms (id, due_date, total_downpayment) "
                 "VALUES (:id, CURRENT_DATE, 20.00)"),
            {"id": terms_id},
        )
    migrate_database(database_engine)
    with database_engine.connect() as connection:
        assert list(connection.scalars(text(
            "SELECT version FROM schema_migrations ORDER BY version"
        ))) == COMMITTED
        assert connection.scalar(text("SELECT id FROM finance_terms")) == terms_id


def test_pending_migration_updates_populated_database(database_engine, tmp_path):
    migrate_database(database_engine)
    with database_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO audit_events (finance_terms_id, action, outcome, details) "
            "VALUES ('00000000-0000-0000-0000-000000000001', 'create', 'succeeded', '{}')"
        ))
    scripts = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, scripts)
    (scripts / "9999_add_source.sql").write_text(
        "ALTER TABLE audit_events ADD COLUMN source TEXT NOT NULL DEFAULT 'api';"
    )
    migrate_database(database_engine, scripts)
    with database_engine.connect() as connection:
        assert connection.scalar(text("SELECT max(version) FROM schema_migrations")) == (
            "9999_add_source.sql"
        )
        assert connection.scalar(text("SELECT source FROM audit_events")) == "api"
        assert connection.scalar(text("SELECT count(*) FROM audit_events")) == 1


def test_failed_migration_rolls_back_schema_and_version_and_can_retry(database_engine, tmp_path):
    migrate_database(database_engine)
    scripts = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, scripts)
    migration = scripts / "9999_add_source.sql"
    migration.write_text(
        "ALTER TABLE audit_events ADD COLUMN source TEXT;\n"
        "INSERT INTO nonexistent_table VALUES (1);"
    )
    with pytest.raises(ProgrammingError):
        migrate_database(database_engine, scripts)
    assert "source" not in {c["name"] for c in inspect(database_engine).get_columns("audit_events")}
    with database_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM schema_migrations")) == len(COMMITTED)
    migration.write_text("ALTER TABLE audit_events ADD COLUMN source TEXT;")
    migrate_database(database_engine, scripts)
    assert "source" in {c["name"] for c in inspect(database_engine).get_columns("audit_events")}


def test_concurrent_startups_apply_migrations_once(database_engine):
    barrier = Barrier(2)

    def start():
        barrier.wait(timeout=10)
        migrate_database(database_engine)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(start) for _ in range(2)]
        for future in futures:
            future.result(timeout=20)
    with database_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM schema_migrations")) == len(COMMITTED)


def test_changed_migration_history_is_rejected(database_engine, tmp_path):
    migrate_database(database_engine)
    (tmp_path / "9999_out_of_order.sql").write_text("DROP TABLE audit_events;")
    with pytest.raises(RuntimeError, match="Migration history"):
        migrate_database(database_engine, tmp_path)
    assert inspect(database_engine).has_table("audit_events")
