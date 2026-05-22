"""Async SQLAlchemy engine and session factory — S002-F-002.

Hard invariant (CLAUDE.md §5): every DB session MUST be async.
No session.query(), no sync sessions, no db = next(get_session()).
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dataplat_api.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    echo=False,
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an AsyncSession, commits on success.

    Usage:
        @router.get("/example")
        async def handler(session: AsyncSession = Depends(get_session)):
            result = await session.execute(select(SomeModel))
    """
    async with SessionLocal() as session:
        yield session
