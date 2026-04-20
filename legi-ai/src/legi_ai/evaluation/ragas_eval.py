"""Ragas-based offline evaluation against a golden question set.

Usage:
    python -m legi_ai.evaluation.ragas_eval --dataset data/eval/golden_set.jsonl
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel

from legi_ai.config import settings
from legi_ai.logging import configure_logging, get_logger

log = get_logger(__name__)

THRESHOLDS = {
    "faithfulness": 0.85,
    "answer_relevancy": 0.80,
    "context_precision": 0.75,
    "context_recall": 0.80,
}


class GoldenItem(BaseModel):
    question: str
    ground_truth: str
    expected_sources: list[str] = []
    category: str | None = None


def _load_golden(path: Path) -> list[GoldenItem]:
    items: list[GoldenItem] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                items.append(GoldenItem.model_validate_json(line))
    return items


def _run_system(items: list[GoldenItem]) -> list[dict[str, Any]]:
    from legi_ai.rag.query_engine import query

    results: list[dict[str, Any]] = []
    for item in items:
        try:
            r = query(item.question)
            results.append(
                {
                    "question": item.question,
                    "answer": r.answer,
                    "contexts": [s.snippet for s in r.sources],
                    "ground_truth": item.ground_truth,
                    "retrieved_ids": [s.doc_id for s in r.sources],
                    "expected_sources": item.expected_sources,
                    "category": item.category,
                }
            )
        except Exception as exc:
            log.error("system error", question=item.question, error=str(exc))
            results.append(
                {
                    "question": item.question,
                    "answer": "",
                    "contexts": [],
                    "ground_truth": item.ground_truth,
                    "expected_sources": item.expected_sources,
                    "error": str(exc),
                }
            )
    return results


def _evaluate(results: list[dict[str, Any]]):
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import (
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )

    ds = Dataset.from_list(
        [
            {
                "question": r["question"],
                "answer": r["answer"],
                "contexts": r["contexts"],
                "ground_truth": r["ground_truth"],
            }
            for r in results
            if r.get("answer")
        ]
    )
    scores = evaluate(
        ds,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
    )
    return scores


def _render_html(results: list[dict[str, Any]], scores: dict[str, float], out_path: Path) -> None:
    rows_html = "\n".join(
        f"<tr><td>{r['question']}</td><td>{r.get('answer', '')[:300]}</td>"
        f"<td>{r.get('category', '')}</td><td>{len(r.get('contexts', []))}</td></tr>"
        for r in results
    )
    metric_rows = "\n".join(
        f"<tr><td>{k}</td><td>{v:.3f}</td>"
        f"<td style='color:{'green' if v >= THRESHOLDS.get(k, 0) else 'red'}'>"
        f"{'PASS' if v >= THRESHOLDS.get(k, 0) else 'FAIL'}</td></tr>"
        for k, v in scores.items()
    )
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>LEGi-AI Ragas Report</title>
<style>body{{font-family:sans-serif;max-width:1200px;margin:2em auto;padding:0 1em}}
table{{width:100%;border-collapse:collapse;margin:1em 0}}
td,th{{border:1px solid #ddd;padding:.5em;text-align:left;vertical-align:top}}
th{{background:#f5f5f5}}</style>
</head><body>
<h1>LEGi-AI RAG Evaluation</h1>
<p>Generated: {datetime.utcnow().isoformat()}Z</p>
<h2>Metrics</h2>
<table><tr><th>Metric</th><th>Score</th><th>Status</th></tr>{metric_rows}</table>
<h2>Samples</h2>
<table><tr><th>Question</th><th>Answer</th><th>Category</th><th>#Contexts</th></tr>{rows_html}</table>
</body></html>"""
    out_path.write_text(html, encoding="utf-8")
    log.info("report written", path=str(out_path))


def run(dataset: Path) -> Path:
    items = _load_golden(dataset)
    log.info("loaded golden", count=len(items))
    results = _run_system(items)

    try:
        scores_obj = _evaluate(results)
        scores = {k: float(v) for k, v in scores_obj.items()}
    except Exception as exc:
        log.error("ragas failed", error=str(exc))
        scores = {k: 0.0 for k in THRESHOLDS}

    out_dir = settings.eval_dir / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"{ts}.html"
    _render_html(results, scores, out_path)

    json_path = out_dir / f"{ts}.json"
    json_path.write_text(json.dumps({"scores": scores, "results": results}, ensure_ascii=False, indent=2))

    failed = {k: v for k, v in scores.items() if v < THRESHOLDS.get(k, 0)}
    if failed:
        log.warning("thresholds not met", failed=failed)
    return out_path


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default=str(settings.eval_dir / "golden_set.jsonl"),
    )
    args = parser.parse_args()
    run(Path(args.dataset))


if __name__ == "__main__":
    main()
