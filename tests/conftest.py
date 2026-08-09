"""Pytest setup: isolated PostgreSQL test database on the running Docker db."""

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
config = dotenv_values(PROJECT_ROOT / ".env")
base_database_url = config.get("DATABASE_URL")
if not base_database_url:
    raise RuntimeError("DATABASE_URL must be configured in .env to run tests")

base_url = make_url(base_database_url)
test_database_name = f"{base_url.database}_test"
test_url = base_url.set(database=test_database_name)

# Create the test database on the same PostgreSQL instance when needed.
maintenance_url = base_url.set(database="postgres")
maintenance_engine = create_engine(maintenance_url)
with maintenance_engine.connect().execution_options(
    isolation_level="AUTOCOMMIT"
) as connection:
    exists = connection.scalar(
        text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
        {"database_name": test_database_name},
    )
    if not exists:
        quoted_name = test_database_name.replace('"', '""')
        connection.exec_driver_sql(f'CREATE DATABASE "{quoted_name}"')
maintenance_engine.dispose()

os.environ["DATABASE_URL"] = test_url.render_as_string(hide_password=False)
# Tests must never use the real network, even when .env contains real provider
# settings. Chat/embedding providers are not implemented in W1-A, but this
# guard keeps future test suites deterministic.
for _name in ("CHAT_PROVIDER", "EMBEDDING_PROVIDER"):
    os.environ.setdefault(_name, "fake")
for _name in (
    "CHAT_API_KEY",
    "CHAT_BASE_URL",
    "CHAT_MODEL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL",
):
    os.environ[_name] = ""

# Re-read settings after the env override; config values are read lazily.
from backend.app import models  # noqa: E402, F401
from backend.app.config import DATABASE_URL  # noqa: E402
from backend.app.database import Base, engine  # noqa: E402

with engine.begin() as connection:
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)


@pytest.fixture(scope="session")
def session_factory():
    """Session factory bound to the freshly created test database."""
    from backend.app.database import SessionLocal

    yield SessionLocal


@pytest.fixture
def db(session_factory):
    """Yield a session and clean all tables after each test."""
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        # Reset tables between tests so tests never share state.
        from backend.app.database import engine

        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE ai_processing_jobs, "
                                    "audit_logs, evaluations, idempotency_keys, "
                                    "knowledge_chunks, knowledge_items, "
                                    "ticket_replies, tickets, users "
                                    "RESTART IDENTITY CASCADE"))
