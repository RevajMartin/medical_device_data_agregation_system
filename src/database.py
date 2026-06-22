"""Database configuration and session management."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings

# Database URL
DATABASE_URL = settings.DATABASE_URL

# asyncpg caches server-side prepared statements per connection by default, which
# breaks under a PgBouncer running in *transaction* pooling mode (a connection is
# handed to a different client each transaction). Disabling the statement cache makes
# every query an unnamed prepared statement -> safe behind a transaction pooler.
# Harmless when connecting straight to Postgres too.
CONNECT_ARGS = {"statement_cache_size": 0}

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_pre_ping=True,
    connect_args=CONNECT_ARGS,
    echo=settings.DEBUG,
)

# Async session factory
async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db():
    """Dependency to get async database session."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def close_db():
    """Close database connections."""
    await engine.dispose()


@asynccontextmanager
async def task_session() -> AsyncIterator[AsyncSession]:
    """Short-lived, isolated session for dead-letter writes (``record_failure``).

    A failure handler must record the failure in its *own* transaction, independent of
    the consumer's session (which is mid-rollback). This creates a NullPool engine on the
    current event loop and disposes it on exit. The caller is responsible for ``commit()``.
    """
    engine = create_async_engine(
        settings.DATABASE_URL, poolclass=NullPool, connect_args=CONNECT_ARGS
    )
    try:
        maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            yield session
    finally:
        await engine.dispose()
