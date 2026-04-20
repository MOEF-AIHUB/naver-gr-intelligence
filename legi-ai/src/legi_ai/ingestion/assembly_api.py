"""Korean National Assembly Open API collector.

Fetches bills, members, and voting records from https://open.assembly.go.kr
and persists results to ``data/raw/`` as Parquet files.

Usage:
    python -m legi_ai.ingestion.assembly_api --target bills --since 2024-05-30
    python -m legi_ai.ingestion.assembly_api --target members
    python -m legi_ai.ingestion.assembly_api --target votes --bill-id PRC_...
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from tqdm.asyncio import tqdm as atqdm

from legi_ai.config import settings
from legi_ai.logging import configure_logging, get_logger

log = get_logger(__name__)

BASE_URL = "https://open.assembly.go.kr/portal/openapi"
PAGE_SIZE = 100

ENDPOINTS = {
    "bills": "nzmimeepazxkubdpn",
    "members": "nwvrqwxyaytdsfvhu",
    "votes": "ncocpgfiaoituanbr",
}


class Bill(BaseModel):
    model_config = ConfigDict(extra="allow")

    bill_id: str = Field(alias="BILL_ID")
    bill_no: str | None = Field(default=None, alias="BILL_NO")
    bill_name: str | None = Field(default=None, alias="BILL_NAME")
    bill_kind: str | None = Field(default=None, alias="BILL_KND")
    proposer: str | None = Field(default=None, alias="RST_PROPOSER")
    proposer_kind: str | None = Field(default=None, alias="PPSR_KND")
    committee: str | None = Field(default=None, alias="COMMITTEE")
    propose_dt: str | None = Field(default=None, alias="PROPOSE_DT")
    proc_result: str | None = Field(default=None, alias="PROC_RESULT")
    link_url: str | None = Field(default=None, alias="LINK_URL")


class Member(BaseModel):
    model_config = ConfigDict(extra="allow")

    mona_cd: str = Field(alias="MONA_CD")
    hg_nm: str | None = Field(default=None, alias="HG_NM")
    poly_nm: str | None = Field(default=None, alias="POLY_NM")
    orig_nm: str | None = Field(default=None, alias="ORIG_NM")
    cmit_nm: str | None = Field(default=None, alias="CMIT_NM")
    reele_gbn_nm: str | None = Field(default=None, alias="REELE_GBN_NM")
    units_nm: str | None = Field(default=None, alias="UNITS_NM")
    sex_gbn_nm: str | None = Field(default=None, alias="SEX_GBN_NM")
    tel_no: str | None = Field(default=None, alias="TEL_NO")
    e_mail: str | None = Field(default=None, alias="E_MAIL")


class Vote(BaseModel):
    model_config = ConfigDict(extra="allow")

    bill_id: str = Field(alias="BILL_ID")
    bill_no: str | None = Field(default=None, alias="BILL_NO")
    bill_name: str | None = Field(default=None, alias="BILL_NAME")
    member_name: str | None = Field(default=None, alias="HG_NM")
    poly_nm: str | None = Field(default=None, alias="POLY_NM")
    vote_result: str | None = Field(default=None, alias="RESULT_VOTE_MOD")
    vote_date: str | None = Field(default=None, alias="VOTE_DATE")


@dataclass
class Collector:
    api_key: str
    client: httpx.AsyncClient

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=16),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _fetch_page(
        self,
        endpoint: str,
        page: int,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "KEY": self.api_key,
            "Type": "json",
            "pIndex": page,
            "pSize": PAGE_SIZE,
        }
        if extra_params:
            params.update(extra_params)

        url = f"{BASE_URL}/{endpoint}"
        resp = await self.client.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _extract_rows(payload: dict[str, Any], endpoint: str) -> tuple[list[dict[str, Any]], int]:
        """Assembly API wraps results in `{endpoint: [{head: [...]}, {row: [...]}]}`."""
        block = payload.get(endpoint)
        if not block or not isinstance(block, list):
            return [], 0

        total = 0
        rows: list[dict[str, Any]] = []
        for item in block:
            if isinstance(item, dict):
                if "head" in item and item["head"]:
                    total = item["head"][0].get("list_total_count", 0)
                elif "row" in item:
                    rows = item["row"]
        return rows, total

    async def paginate(
        self,
        endpoint: str,
        extra_params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        first = await self._fetch_page(endpoint, 1, extra_params)
        rows, total = self._extract_rows(first, endpoint)
        if total <= PAGE_SIZE:
            return rows

        total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        log.info("paginating", endpoint=endpoint, total=total, pages=total_pages)

        tasks = [
            self._fetch_page(endpoint, page, extra_params)
            for page in range(2, total_pages + 1)
        ]
        for coro in atqdm.as_completed(tasks, desc=endpoint):
            payload = await coro
            page_rows, _ = self._extract_rows(payload, endpoint)
            rows.extend(page_rows)

        return rows


def _save_parquet(rows: list[BaseModel], target: str) -> Path:
    if not rows:
        log.warning("no rows to save", target=target)
        return Path()

    df = pd.DataFrame([r.model_dump(by_alias=True) for r in rows])
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    out_dir = settings.raw_dir / target
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date_str}.parquet"
    df.to_parquet(out_path, index=False)
    log.info("saved", target=target, rows=len(df), path=str(out_path))
    return out_path


async def collect_bills(since: date | None = None) -> Path:
    async with httpx.AsyncClient() as client:
        collector = Collector(api_key=settings.assembly_api_key, client=client)
        params: dict[str, Any] = {"AGE": "22"}
        if since:
            params["PROPOSE_DT"] = since.isoformat()
        raw = await collector.paginate(ENDPOINTS["bills"], params)

    bills: list[BaseModel] = []
    for row in raw:
        try:
            bills.append(Bill.model_validate(row))
        except Exception as exc:
            log.warning("bill parse failed", error=str(exc), row=row)
    return _save_parquet(bills, "bills")


async def collect_members() -> Path:
    async with httpx.AsyncClient() as client:
        collector = Collector(api_key=settings.assembly_api_key, client=client)
        raw = await collector.paginate(ENDPOINTS["members"])

    members: list[BaseModel] = []
    for row in raw:
        try:
            members.append(Member.model_validate(row))
        except Exception as exc:
            log.warning("member parse failed", error=str(exc), row=row)
    return _save_parquet(members, "members")


async def collect_votes(bill_id: str) -> Path:
    async with httpx.AsyncClient() as client:
        collector = Collector(api_key=settings.assembly_api_key, client=client)
        raw = await collector.paginate(ENDPOINTS["votes"], {"BILL_ID": bill_id})

    votes: list[BaseModel] = []
    for row in raw:
        try:
            votes.append(Vote.model_validate(row))
        except Exception as exc:
            log.warning("vote parse failed", error=str(exc), row=row)
    return _save_parquet(votes, f"votes/{bill_id}")


Target = Literal["bills", "members", "votes"]


async def run(target: Target, since: str | None, bill_id: str | None) -> Path:
    if target == "bills":
        since_date = date.fromisoformat(since) if since else date(2024, 5, 30)
        return await collect_bills(since_date)
    if target == "members":
        return await collect_members()
    if target == "votes":
        if not bill_id:
            raise ValueError("--bill-id is required for votes target")
        return await collect_votes(bill_id)
    raise ValueError(f"unknown target: {target}")


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Assembly Open API collector")
    parser.add_argument("--target", required=True, choices=["bills", "members", "votes"])
    parser.add_argument("--since", default=None, help="ISO date (YYYY-MM-DD) for bills")
    parser.add_argument("--bill-id", default=None, help="Bill ID for votes target")
    args = parser.parse_args()

    if not settings.assembly_api_key:
        raise SystemExit("ASSEMBLY_API_KEY is not set in environment")

    asyncio.run(run(args.target, args.since, args.bill_id))


if __name__ == "__main__":
    main()
