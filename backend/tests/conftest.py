import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from tokenpilot.config import Settings
from tokenpilot.db import make_engine, make_session_factory
from tokenpilot.web.app import create_app

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Tests run against a separate database (created by docker/init-test-db.sql).
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg2://tokenpilot:tokenpilot@localhost:5433/tokenpilot_test",
)
LEDGER_TABLES = (
    "fct_usage, fct_billed_cost, import_batch, dim_model_price, dim_model, "
    "dim_api_key, dim_scope, dim_date"
)


@pytest.fixture
def app():
    return create_app(Settings(database_url=TEST_DATABASE_URL))


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(scope="session")
def engine():
    """Migrate the test database once per run — this also tests the migrations."""
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "head")
    engine = make_engine(TEST_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    """A clean ledger for every test."""
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {LEDGER_TABLES} RESTART IDENTITY CASCADE"))
    with make_session_factory(engine)() as session:
        yield session
