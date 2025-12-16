import os
import asyncpg
from typing import Any, Dict, List, Optional

_pool: Optional[asyncpg.pool.Pool] = None

def get_db_url() -> str:
    url = os.getenv("SUPABASE_DB_URL", "").strip()
    if not url:
        raise RuntimeError("SUPABASE_DB_URL is not set. Copy .env.example to .env and fill it.")
    return url

async def get_pool() -> asyncpg.pool.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=get_db_url(), min_size=1, max_size=10)
    return _pool

async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None

async def execute(sql: str, *args) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(sql, *args)

async def fetchrow(sql: str, *args) -> Optional[asyncpg.Record]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(sql, *args)

async def fetch(sql: str, *args) -> List[asyncpg.Record]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(sql, *args)

async def upsert_session(session_id: str, user_id: Optional[str] = None) -> None:
    await execute(
        """
        insert into sessions(session_id, user_id)
        values($1, $2)
        on conflict (session_id) do update set user_id = coalesce(excluded.user_id, sessions.user_id)
        """,
        session_id, user_id
    )

async def log_event(
    session_id: str,
    event_type: str,
    role: Optional[str],
    content: str,
    meta: Optional[Dict[str, Any]] = None
) -> None:
    meta = meta or {}
    await execute(
        """
        insert into session_events(session_id, event_type, role, content, meta)
        values($1, $2, $3, $4, $5::jsonb)
        """,
        session_id, event_type, role, content, json_dumps(meta)
    )

def json_dumps(obj: Any) -> str:
    # asyncpg expects jsonb as text
    import json
    return json.dumps(obj, ensure_ascii=False)

async def get_transcript(session_id: str) -> List[Dict[str, str]]:
    rows = await fetch(
        """
        select ts, event_type, role, content, meta
        from session_events
        where session_id = $1
        order by ts asc, id asc
        """,
        session_id
    )
    out: List[Dict[str, str]] = []
    for r in rows:
        out.append({
            "role": (r["role"] or "system"),
            "content": r["content"],
            "event_type": r["event_type"]
        })
    return out

async def finalize_session(session_id: str, summary: str) -> None:
    # Compute duration_seconds in SQL for correctness
    await execute(
        """
        update sessions
        set end_time = now(),
            duration_seconds = extract(epoch from (now() - start_time))::int,
            summary = $2
        where session_id = $1
        """,
        session_id, summary
    )
