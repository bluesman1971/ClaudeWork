import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Alembic Config object — access to alembic.ini values.
config = context.config

# Set up Python logging from alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Point Alembic at our models ───────────────────────────────────────────────
# Import the declarative base from models so autogenerate can diff against the
# current schema.
from models import db  # noqa: E402
target_metadata = db.metadata

# ── Database URL ──────────────────────────────────────────────────────────────
# Read from environment (mirrors database.py's _safe_db_url logic).
_raw_url = os.getenv('DATABASE_URL', 'sqlite:///trip_master.db')
if _raw_url.startswith('postgres://'):
    _raw_url = 'postgresql://' + _raw_url[len('postgres://'):]

# Override whatever is in alembic.ini — we never hardcode credentials.
config.set_main_option('sqlalchemy.url', _raw_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generate SQL script without a live DB)."""
    url = config.get_main_option('sqlalchemy.url')
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
