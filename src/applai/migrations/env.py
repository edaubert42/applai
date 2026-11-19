"""Alembic runs this to migrate: on the connection open_database() passes, in its transaction."""

from alembic import context

from applai.models import Base

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Migrations run through applai.database.open_database().")
# SQLite alters few things in place: autogenerate writes batch operations that copy the table.
context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
