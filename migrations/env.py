from alembic import context

from aix.db import Base, database_url, engine
from aix.main import app  # Registers the models included by the API.


def run_migrations():
    if context.is_offline_mode():
        context.configure(
            url=database_url().render_as_string(hide_password=False),
            target_metadata=Base.metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        with engine().connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()


run_migrations()
