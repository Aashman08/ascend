"""Database configuration and application startup."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import sessionmaker


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    test_database_url: str


settings = Settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def migrate_database(
    database_engine: Engine = engine, directory: Path = MIGRATIONS_DIR
) -> None:
    """Apply numbered SQL files atomically; a failure prevents application startup."""
    scripts = sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    if not scripts:
        raise RuntimeError(f"No SQL migrations found in {directory}")
    with database_engine.begin() as connection:
        # Only one server process may migrate this database at a time.
        connection.execute(text("SELECT pg_advisory_xact_lock(734982150)"))
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        ))
        applied = list(connection.scalars(text(
            "SELECT version FROM schema_migrations ORDER BY version"
        )))
        if applied != [script.name for script in scripts[:len(applied)]]:
            raise RuntimeError("Migration history does not match the ordered SQL files")
        for script in scripts[len(applied):]:
            connection.exec_driver_sql(script.read_text(encoding="utf-8"))
            connection.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                {"version": script.name},
            )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    migrate_database()
    try:
        yield
    finally:
        engine.dispose()
