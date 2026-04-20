"""Structured JSONL query log + daily/weekly cost report."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from legi_ai.config import settings
from legi_ai.logging import get_logger

log = get_logger(__name__)

LOG_PATH = settings.logs_dir / "queries.jsonl"

OPUS_INPUT_USD_PER_MTOK = 15.0
OPUS_OUTPUT_USD_PER_MTOK = 75.0
HAIKU_INPUT_USD_PER_MTOK = 1.0
HAIKU_OUTPUT_USD_PER_MTOK = 5.0


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    if "haiku" in model:
        return (
            input_tokens / 1_000_000 * HAIKU_INPUT_USD_PER_MTOK
            + output_tokens / 1_000_000 * HAIKU_OUTPUT_USD_PER_MTOK
        )
    return (
        input_tokens / 1_000_000 * OPUS_INPUT_USD_PER_MTOK
        + output_tokens / 1_000_000 * OPUS_OUTPUT_USD_PER_MTOK
    )


def append(entry: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def logged(func: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(func)
    def wrapper(question: str, **kwargs: Any) -> Any:
        result = func(question, **kwargs)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "question": question,
            "answer": getattr(result, "answer", None),
            "sources": [s.doc_id for s in getattr(result, "sources", [])],
            "confidence": getattr(result, "confidence", None),
            "latency_ms": getattr(result, "latency_ms", None),
            "model": getattr(result, "model", None),
        }
        append(entry)
        return result

    return wrapper


def report(days: int = 7) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    by_day: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"queries": 0, "latency_sum_ms": 0, "cost_usd": 0.0}
    )
    if not LOG_PATH.exists():
        return {"range_days": days, "days": {}, "total_queries": 0}

    with LOG_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            entry = json.loads(line)
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts < cutoff:
                continue
            day = ts.date().isoformat()
            by_day[day]["queries"] += 1
            by_day[day]["latency_sum_ms"] += entry.get("latency_ms", 0)

    total = sum(d["queries"] for d in by_day.values())
    return {"range_days": days, "total_queries": total, "days": by_day}


def main() -> None:
    parser = argparse.ArgumentParser(description="Query log cost/volume report")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    data = report(args.days)
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
