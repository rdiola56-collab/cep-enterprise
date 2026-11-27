import os

from alembic import context
from sqlalchemy import create_engine

from app.db.models import Base

url = context.config.get_main_option("sqlalchemy.url") or os.environ.get(
    "CEP_DATABASE_URL", "postgresql+psycopg://cep:cep@localhost:5432/cep"
)


def run() -> None:
    connectable = create_engine(url)
    with connectable.connect() as conn:
        context.configure(connection=conn, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run()
