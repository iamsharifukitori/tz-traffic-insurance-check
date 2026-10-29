import os

from alembic import context
from sqlalchemy import create_engine, pool

from fleet.db import Base

target_metadata = Base.metadata
url = os.environ.get("DATABASE_URL") or context.config.get_main_option("sqlalchemy.url")
if not url:
    raise RuntimeError("Set DATABASE_URL before running migrations.")

provided_connection = context.config.attributes.get("connection")
if provided_connection is not None:
    context.configure(connection=provided_connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
elif context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
