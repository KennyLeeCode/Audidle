"""Database engine and session management.

SQLite by default, which keeps local development to zero setup. The models use
no SQLite specific types, so moving to Postgres is a connection URL change plus
installing asyncpg.

Async throughout, because the rest of the stack is async and a blocking database
call inside a FastAPI request would stall the event loop.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database.models import Base


def build_engine(database_url: str, echo: bool = False) -> AsyncEngine:
    """Create the async engine for a connection URL."""
    return create_async_engine(database_url, echo=echo, future=True)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create the session factory.

    expire_on_commit is off so objects stay usable after a commit, which matters
    because callers map them to domain models after the transaction closes.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_schema(engine: AsyncEngine) -> None:
    """Create tables that do not exist yet.

    Adequate for a single table with no migration history. A schema change that
    needs to preserve data should bring in Alembic rather than extending this.
    """
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Transactional scope around a series of operations."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
