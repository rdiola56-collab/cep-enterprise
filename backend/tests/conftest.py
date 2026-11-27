import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

ADMIN_URL = os.environ.get("CEP_TEST_ADMIN_URL", "postgresql+psycopg://postgres@localhost:5432/postgres")
TEST_DB = "cep_pytest"


@pytest.fixture(scope="session")
def db_engine():
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            c.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
            c.execute(text(f"CREATE DATABASE {TEST_DB}"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL not available: {exc}")
    url = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}"
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    eng = create_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture
def db(db_engine):
    """A session inside a transaction that is rolled back after the test."""
    conn = db_engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    yield s
    s.close()
    trans.rollback()
    conn.close()
