"""Alembic async env.py — S002-F-002.

Uses SQLAlchemy 2.x async engine pattern:
  create_async_engine → engine.begin() → conn.run_sync(do_run_migrations)

Offline mode is intentionally unsupported: DATABASE_URL must always be
reachable (the container has it set; the host developer uses the container).
"""

import asyncio
import os

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

# Import Base so target_metadata is wired for future --autogenerate runs.
# This import is intentional and MUST stay — see agreed.md §4.
from dataplat_api.db.models import Base

config = context.config

# Override sqlalchemy.url from DATABASE_URL environment variable.
# This is the only supported connection path — alembic.ini's placeholder
# is never used directly.
config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])

target_metadata = Base.metadata


def do_run_migrations(connection):  # type: ignore[no-untyped-def]
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = config.get_main_option("sqlalchemy.url")
    assert url is not None, "sqlalchemy.url must be set in alembic.ini or via env.py"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    raise RuntimeError(
        "Offline mode is not supported — DATABASE_URL must be reachable. "
        "Run migrations inside the fastapi container: "
        "docker compose exec fastapi uv run alembic upgrade head"
    )
else:
    run_migrations_online()
