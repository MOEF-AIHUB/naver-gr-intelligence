"""Normalize raw ingestion outputs into a unified Document schema.

Reads Parquet/JSON from ``data/raw/`` and writes ``data/processed/documents.jsonl``
with deduplicated, LlamaIndex-compatible Documents.

Usage:
    python -m legi_ai.ingestion.normalizer
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field

from legi_ai.config import settings
from legi_ai.logging import configure_logging, get_logger

log = get_logger(__name__)

SourceType = Literal["bill", "member", "law", "article"]


class Document(BaseModel):
    doc_id: str
    source_type: SourceType
    title: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    valid_from: str | None = None
    valid_to: str | None = None

    def to_llama_document(self):  # pragma: no cover - requires llama-index
        from llama_index.core import Document as LIDoc

        return LIDoc(
            doc_id=self.doc_id,
            text=self.content,
            metadata={
                "title": self.title,
                "source_type": self.source_type,
                "valid_from": self.valid_from,
                **self.metadata,
            },
        )


def _hash_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def normalize_bills(path: Path) -> list[Document]:
    df = pd.read_parquet(path)
    docs: list[Document] = []
    for row in df.to_dict(orient="records"):
        bill_id = row.get("BILL_ID") or row.get("bill_id") or ""
        if not bill_id:
            continue
        name = row.get("BILL_NAME") or ""
        committee = row.get("COMMITTEE") or ""
        proposer = row.get("RST_PROPOSER") or ""
        propose_dt = row.get("PROPOSE_DT") or ""
        proc = row.get("PROC_RESULT") or ""

        content = "\n".join(
            [
                f"# {name}",
                f"- 의안번호: {row.get('BILL_NO', '')}",
                f"- 대표발의: {proposer}",
                f"- 소관위원회: {committee}",
                f"- 제안일: {propose_dt}",
                f"- 처리결과: {proc}",
                "",
                "## 주요내용",
                row.get("SUMMARY", "") or "(본문 미수집 — 의안 상세 페이지 참조)",
            ]
        )

        docs.append(
            Document(
                doc_id=_hash_id("bill", bill_id),
                source_type="bill",
                title=name or bill_id,
                content=content,
                metadata={
                    "bill_id": bill_id,
                    "bill_no": row.get("BILL_NO"),
                    "committee": committee,
                    "proposer": proposer,
                    "proc_result": proc,
                    "link_url": row.get("LINK_URL"),
                },
                valid_from=propose_dt or None,
            )
        )
    return docs


def normalize_members(path: Path) -> list[Document]:
    df = pd.read_parquet(path)
    docs: list[Document] = []
    for row in df.to_dict(orient="records"):
        mona = row.get("MONA_CD") or ""
        if not mona:
            continue
        name = row.get("HG_NM") or ""
        party = row.get("POLY_NM") or ""
        district = row.get("ORIG_NM") or ""
        committee = row.get("CMIT_NM") or ""

        content = "\n".join(
            [
                f"# {name} 의원",
                f"- 정당: {party}",
                f"- 선거구: {district}",
                f"- 소속위원회: {committee}",
                f"- 당선: {row.get('REELE_GBN_NM', '')}",
                f"- 대수: {row.get('UNITS_NM', '')}",
            ]
        )

        docs.append(
            Document(
                doc_id=_hash_id("member", mona),
                source_type="member",
                title=f"{name} 의원",
                content=content,
                metadata={
                    "member_id": mona,
                    "name": name,
                    "party": party,
                    "district": district,
                    "committee": committee,
                },
            )
        )
    return docs


def normalize_law(path: Path) -> list[Document]:
    """One Document per article (조문) + one summary doc per law."""
    data = json.loads(path.read_text(encoding="utf-8"))
    law_id = data["law_id"]
    law_name = data["law_name"]
    ministry = data.get("ministry") or ""
    enforce = data.get("enforcement_date") or ""

    docs: list[Document] = [
        Document(
            doc_id=_hash_id("law", law_id, enforce),
            source_type="law",
            title=law_name,
            content="\n".join(
                [
                    f"# {law_name}",
                    f"- 법령ID: {law_id}",
                    f"- 소관부처: {ministry}",
                    f"- 시행일: {enforce}",
                    f"- 공포일: {data.get('promulgation_date', '')}",
                    f"- 공포번호: {data.get('promulgation_no', '')}",
                    f"- 조문 수: {len(data.get('articles', []))}",
                ]
            ),
            metadata={
                "law_id": law_id,
                "law_name": law_name,
                "ministry": ministry,
            },
            valid_from=enforce or None,
        )
    ]

    for art in data.get("articles", []):
        article_no = art["article_no"]
        title = art.get("article_title") or ""
        body = art.get("content") or ""
        parts: list[str] = [f"# {law_name} {article_no}"]
        if title:
            parts.append(f"## {title}")
        if body:
            parts.append(body)
        for para in art.get("paragraphs", []):
            p_line = f"{para.get('no', '')} {para.get('content', '')}".strip()
            if p_line:
                parts.append(p_line)
            for item in para.get("items", []):
                i_line = f"  {item.get('no', '')} {item.get('content', '')}".strip()
                if i_line:
                    parts.append(i_line)

        docs.append(
            Document(
                doc_id=_hash_id("article", law_id, article_no, enforce),
                source_type="article",
                title=f"{law_name} {article_no}",
                content="\n".join(parts),
                metadata={
                    "law_id": law_id,
                    "law_name": law_name,
                    "article_no": article_no,
                    "article_title": title,
                    "ministry": ministry,
                },
                valid_from=enforce or None,
            )
        )
    return docs


def run() -> Path:
    raw = settings.raw_dir
    all_docs: dict[str, Document] = {}

    bills_dir = raw / "bills"
    if bills_dir.exists():
        for pq in sorted(bills_dir.glob("*.parquet")):
            for d in normalize_bills(pq):
                all_docs[d.doc_id] = d

    members_dir = raw / "members"
    if members_dir.exists():
        for pq in sorted(members_dir.glob("*.parquet")):
            for d in normalize_members(pq):
                all_docs[d.doc_id] = d

    laws_dir = raw / "laws"
    if laws_dir.exists():
        for js in sorted(laws_dir.glob("*/*.json")):
            for d in normalize_law(js):
                all_docs[d.doc_id] = d

    out_path = settings.processed_dir / "documents.jsonl"
    with out_path.open("w", encoding="utf-8") as fh:
        for d in all_docs.values():
            fh.write(d.model_dump_json() + "\n")

    by_type: dict[str, int] = {}
    for d in all_docs.values():
        by_type[d.source_type] = by_type.get(d.source_type, 0) + 1

    log.info("normalized", total=len(all_docs), by_type=by_type, path=str(out_path))
    return out_path


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Normalize raw data to unified Document schema")
    parser.parse_args()
    run()


if __name__ == "__main__":
    main()
