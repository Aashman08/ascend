"""Integration tests use disposable schemas; pure tests never connect to Postgres."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app import client as client_module
from app.database import migrate_database, settings
from app.server import app


@pytest.fixture
def database_engine():
    """Create an isolated PostgreSQL schema for one test."""
    schema = f"test_{uuid.uuid4().hex}"
    admin = create_engine(settings.test_database_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        settings.test_database_url, connect_args={"options": f"-csearch_path={schema}"}
    )
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def session_factory(database_engine):
    """Create the application tables and return a session factory for the schema."""
    migrate_database(database_engine)
    return sessionmaker(bind=database_engine, expire_on_commit=False)


@pytest.fixture
def db(session_factory) -> Iterator[Session]:
    """Provide a database session for direct test assertions."""
    with session_factory() as session:
        yield session


@pytest.fixture
def client(session_factory, monkeypatch) -> Iterator[TestClient]:
    """Provide an HTTP client connected to the isolated test database."""
    monkeypatch.setattr(client_module, "SessionLocal", session_factory)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        client.close()
