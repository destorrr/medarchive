"""Alembic environment with optional dedicated migration credentials."""
import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
migration_url = os.environ.get("MEDARCHIVE_MIGRATION_DATABASE_URL")
url = migration_url or os.environ.get("MEDARCHIVE_DATABASE_URL", "postgresql+asyncpg://medarchive:medarchive@localhost:5432/medarchive")
config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
target_metadata = None


def run_migrations_offline() -> None:
    if migration_url:
        raise RuntimeError("Dedicated migrations require online identity verification; offline SQL is not supported")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    if migration_url:
        if connection.scalar(text("SELECT session_user")) != "medarchive_migrator":
            raise RuntimeError("Dedicated migration connection must use medarchive_migrator")
        connection.execute(text("SET ROLE medarchive_owner"))
        connection.commit()
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
