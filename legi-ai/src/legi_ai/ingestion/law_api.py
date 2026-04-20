"""Korean Law Information Center (law.go.kr) API collector.

Fetches statutes (법령) by name, parses articles (조문) in a structured
hierarchy (제N조 > 제N항 > 제N호), and persists results as JSON keyed by
law id + enforcement date.

Usage:
    python -m legi_ai.ingestion.law_api --laws 조세특례제한법 소득세법 법인세법
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from tqdm.asyncio import tqdm as atqdm

from legi_ai.config import settings
from legi_ai.logging import configure_logging, get_logger

log = get_logger(__name__)

BASE_URL = "https://www.law.go.kr/DRF"
DEFAULT_LAWS = [
    "조세특례제한법",
    "소득세법",
    "법인세법",
    "국유재산법",
    "벤처투자 촉진에 관한 법률",
]


class LawArticle(BaseModel):
    article_no: str
    article_title: str | None = None
    content: str
    paragraphs: list[dict[str, Any]] = Field(default_factory=list)


class Law(BaseModel):
    law_id: str
    law_name: str
    law_name_ko: str | None = None
    ministry: str | None = None
    enforcement_date: str | None = None
    promulgation_date: str | None = None
    promulgation_no: str | None = None
    articles: list[LawArticle] = Field(default_factory=list)
    raw_xml: str | None = None


@dataclass
class LawCollector:
    api_key: str
    client: httpx.AsyncClient

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=16),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _get(self, endpoint: str, params: dict[str, Any]) -> str:
        params = {**params, "OC": self.api_key}
        resp = await self.client.get(f"{BASE_URL}/{endpoint}", params=params, timeout=30)
        resp.raise_for_status()
        return resp.text

    async def search_law(self, query: str) -> str | None:
        """Returns the first matching law MST (일련번호)."""
        xml_text = await self._get(
            "lawSearch.do",
            {"target": "law", "query": query, "type": "XML", "display": "5"},
        )
        root = ET.fromstring(xml_text)
        first = root.find(".//law")
        if first is None:
            return None
        mst_el = first.find("법령일련번호")
        return mst_el.text if mst_el is not None else None

    async def fetch_law_xml(self, mst: str) -> str:
        return await self._get(
            "lawService.do",
            {"target": "law", "MST": mst, "type": "XML"},
        )


_ARTICLE_NO_RE = re.compile(r"제\s*(\d+)\s*조(?:의\s*(\d+))?")


def _clean_text(raw: str | None) -> str:
    if not raw:
        return ""
    return re.sub(r"\s+", " ", raw).strip()


def parse_law_xml(xml_text: str) -> Law:
    root = ET.fromstring(xml_text)

    basic = root.find("기본정보")
    law_id = _find_text(basic, "법령ID") or _find_text(basic, "법령일련번호") or "unknown"
    law_name = _find_text(basic, "법령명_한글") or _find_text(basic, "법령명한글") or "unknown"
    ministry = _find_text(basic, "소관부처")
    enforce = _find_text(basic, "시행일자")
    promul = _find_text(basic, "공포일자")
    promul_no = _find_text(basic, "공포번호")

    articles: list[LawArticle] = []
    for art in root.findall(".//조문/조문단위"):
        no = _find_text(art, "조문번호") or ""
        sub_no = _find_text(art, "조문가지번호")
        title = _find_text(art, "조문제목")
        body = _find_text(art, "조문내용")

        article_no = f"제{no}조" + (f"의{sub_no}" if sub_no and sub_no != "0" else "")
        paragraphs: list[dict[str, Any]] = []
        for para in art.findall(".//항"):
            p_no = _find_text(para, "항번호") or ""
            p_content = _find_text(para, "항내용") or ""
            items: list[dict[str, Any]] = []
            for item in para.findall(".//호"):
                items.append(
                    {
                        "no": _find_text(item, "호번호") or "",
                        "content": _clean_text(_find_text(item, "호내용")),
                    }
                )
            paragraphs.append(
                {
                    "no": p_no,
                    "content": _clean_text(p_content),
                    "items": items,
                }
            )

        articles.append(
            LawArticle(
                article_no=article_no,
                article_title=title,
                content=_clean_text(body),
                paragraphs=paragraphs,
            )
        )

    return Law(
        law_id=str(law_id),
        law_name=law_name,
        law_name_ko=law_name,
        ministry=ministry,
        enforcement_date=enforce,
        promulgation_date=promul,
        promulgation_no=promul_no,
        articles=articles,
        raw_xml=xml_text,
    )


def _find_text(elem: ET.Element | None, tag: str) -> str | None:
    if elem is None:
        return None
    found = elem.find(f".//{tag}")
    if found is None or found.text is None:
        return None
    return found.text.strip()


async def collect_laws(law_names: list[str]) -> list[Law]:
    if not settings.law_api_key:
        raise SystemExit("LAW_API_KEY is not set in environment")

    async with httpx.AsyncClient() as client:
        collector = LawCollector(api_key=settings.law_api_key, client=client)

        async def _one(name: str) -> Law | None:
            mst = await collector.search_law(name)
            if not mst:
                log.warning("law not found", query=name)
                return None
            xml_text = await collector.fetch_law_xml(mst)
            try:
                return parse_law_xml(xml_text)
            except Exception as exc:
                log.warning("parse failed", query=name, error=str(exc))
                return None

        results: list[Law] = []
        for coro in atqdm.as_completed([_one(n) for n in law_names], desc="laws"):
            law = await coro
            if law:
                _save_law(law)
                results.append(law)
        return results


def _save_law(law: Law) -> None:
    enforce = law.enforcement_date or "unknown"
    out_dir = settings.raw_dir / "laws" / law.law_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{enforce}.json"
    out_path.write_text(
        law.model_dump_json(exclude={"raw_xml"}, indent=2),
        encoding="utf-8",
    )
    xml_path = out_dir / f"{enforce}.xml"
    if law.raw_xml:
        xml_path.write_text(law.raw_xml, encoding="utf-8")
    log.info("saved law", law_name=law.law_name, articles=len(law.articles), path=str(out_path))


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="law.go.kr collector")
    parser.add_argument("--laws", nargs="*", default=DEFAULT_LAWS)
    args = parser.parse_args()
    asyncio.run(collect_laws(args.laws))


if __name__ == "__main__":
    main()
