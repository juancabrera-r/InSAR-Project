from collections.abc import MutableMapping
from logging.config import fileConfig
from typing import Literal

# Import models so SQLAlchemy registers their tables.
import insar_platform.database.models  # noqa: F401
from alembic import context
from insar_platform.database.base import Base
from insar_platform.database.connection import engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_name(
    name: str | None,
    type_: Literal[
        "schema",
        "table",
        "column",
        "index",
        "unique_constraint",
        "foreign_key_constraint",
        "check_constraint",
    ],
    parent_names: MutableMapping[
        Literal[
            "schema_name",
            "table_name",
            "schema_qualified_table_name",
        ],
        str | None,
    ],
) -> bool:
    if type_ == "schema":
        return name is None

    if type_ == "table":
        return name is not None and name in target_metadata.tables

    return True


def run_migrations_offline() -> None:
    context.configure(
        url=engine.url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        include_name=include_name,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_name=include_name,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
