---
name: fastapi-async
description: Mandatory patterns for async SQLAlchemy sessions in apps/api. Read whenever writing or modifying any code that touches the DB.
---

# Async session — non-negotiable

§11.7 #1 of the design doc: **同步 session 在 IO 密集场景一上量就崩，迁移成本极高**. We use async from day one.

## Canonical session setup

```python
# apps/api/dataplat_api/db.py
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

engine = create_async_engine(settings.DB_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
```

## Route pattern

```python
from sqlalchemy import select

@router.get("/repos/{repo_id}")
async def get_repo(
    repo_id: str,
    session: AsyncSession = Depends(get_session),
) -> RepoOut:
    result = await session.execute(select(Repo).where(Repo.id == repo_id))
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(404)
    return RepoOut.model_validate(repo)
```

## Hard NOs

- `session.query(...)` — that's the sync API. Always `select(...)` + `await session.execute(...)`.
- `session.commit()` without `await`.
- Mixing sync and async sessions in the same flow.
- Background tasks grabbing `SessionLocal()` without `async with`.
- `db = next(get_session())` — that's a sync-iterator pattern, will break.

## When relationships need loading

Use `selectinload` / `joinedload`:

```python
from sqlalchemy.orm import selectinload

stmt = select(Repo).options(selectinload(Repo.commits))
```

Never assume lazy loading works — it doesn't in async context without explicit `session.run_sync`.

## Testing

Use `pytest-asyncio` + a transactional fixture that rolls back per test:

```python
@pytest_asyncio.fixture
async def session(engine):
    async with AsyncSession(engine) as s:
        yield s
        await s.rollback()
```
