from alembic import context

from app.config import DATABASE_URL
from app.db.models import Base
from app.db.session import apply_views, engine

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL, target_metadata=target_metadata, literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
            # Views are not versioned individually; they are rebuilt from
            # views.sql after every migration so they can never drift.
            apply_views(connection, strict=False)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
