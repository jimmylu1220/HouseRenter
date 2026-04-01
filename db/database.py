import asyncpg
from config import DATABASE_URL

_pool: asyncpg.Pool | None = None


async def init_db() -> None:
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    async with _pool.acquire() as conn:
        from db.migrate import run_migrations
        await run_migrations(conn)


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    return _pool


async def close_db() -> None:
    if _pool:
        await _pool.close()
