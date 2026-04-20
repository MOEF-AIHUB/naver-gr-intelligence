"""SQLite-backed query response cache with per-source-type TTL."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from legi_ai.config import settings
from legi_ai.logging import get_logger

log = get_logger(__name__)

CACHE_PATH = settings.logs_dir / "query_cache.sqlite"
DEFAULT_TTL = int(timedelta(hours=24).total_seconds())
LAW_TTL = int(timedelta(days=7).total_seconds())


def _init_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS query_cache (
            key TEXT PRIMARY KEY,
            response TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            ttl_seconds INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


@contextmanager
def _conn():
    _init_db(CACHE_PATH)
    c = sqlite3.connect(CACHE_PATH)
    try:
        yield c
    finally:
        c.commit()
        c.close()


def _key(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def get(payload: dict[str, Any]) -> dict[str, Any] | None:
    key = _key(payload)
    with _conn() as c:
        row = c.execute(
            "SELECT response, created_at, ttl_seconds FROM query_cache WHERE key=?",
            (key,),
        ).fetchone()
    if not row:
        return None
    response, created, ttl = row
    if time.time() - created > ttl:
        return None
    return json.loads(response)


def put(payload: dict[str, Any], response: dict[str, Any], ttl: int = DEFAULT_TTL) -> None:
    key = _key(payload)
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO query_cache VALUES (?, ?, ?, ?)",
            (key, json.dumps(response, ensure_ascii=False), int(time.time()), ttl),
        )


def cached(ttl_resolver: Callable[[dict[str, Any]], int] | None = None):
    """Decorator for ``query_engine.query``-style functions."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(question: str, **kwargs: Any) -> Any:
            payload = {"question": question, **kwargs}
            cached_result = get(payload)
            if cached_result is not None:
                log.info("cache hit", question=question[:60])
                from legi_ai.rag.query_engine import QueryResult

                return QueryResult.model_validate(cached_result)

            result = func(question, **kwargs)
            dumped = result.model_dump() if hasattr(result, "model_dump") else result
            ttl = ttl_resolver(payload) if ttl_resolver else DEFAULT_TTL
            put(payload, dumped, ttl=ttl)
            return result

        return wrapper

    return decorator


def default_ttl_resolver(payload: dict[str, Any]) -> int:
    q = str(payload.get("question", "")).lower()
    if any(k in q for k in ["법", "조문", "시행령", "시행규칙"]):
        return LAW_TTL
    return DEFAULT_TTL
