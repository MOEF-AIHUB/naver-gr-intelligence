"""Weekly issue tracker: bills, law changes, and related member activity.

Usage:
    python -m legi_ai.agents.issue_tracker --keywords 플랫폼 AI규제 --weeks 4
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Literal, TypedDict

import pandas as pd
from pydantic import BaseModel, Field

from legi_ai.agents.common import get_llm
from legi_ai.config import settings
from legi_ai.logging import configure_logging, get_logger

log = get_logger(__name__)

DEFAULT_KEYWORDS = ["플랫폼", "AI", "개인정보", "클라우드", "벤처투자"]


class IssueAlert(BaseModel):
    level: Literal["critical", "high", "medium", "info"]
    reason: str
    recommendation: str


class TrendBucket(BaseModel):
    period: str
    bill_count: int
    keywords: list[str]


class IssueReport(BaseModel):
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    keywords: list[str]
    weeks: int
    new_bills: list[dict[str, Any]] = Field(default_factory=list)
    law_changes: list[dict[str, Any]] = Field(default_factory=list)
    member_activity: list[dict[str, Any]] = Field(default_factory=list)
    trend: list[TrendBucket] = Field(default_factory=list)
    alert: IssueAlert | None = None
    summary_markdown: str = ""


class IssueState(TypedDict, total=False):
    keywords: list[str]
    weeks: int
    new_bills: list[dict[str, Any]]
    law_changes: list[dict[str, Any]]
    member_activity: list[dict[str, Any]]
    trend: list[TrendBucket]
    alert: IssueAlert
    report: IssueReport


def _load_bills() -> pd.DataFrame:
    bills_dir = settings.raw_dir / "bills"
    if not bills_dir.exists():
        return pd.DataFrame()
    latest = sorted(bills_dir.glob("*.parquet"))[-1]
    return pd.read_parquet(latest)


def _match(df: pd.DataFrame, keywords: list[str]) -> pd.DataFrame:
    if df.empty or "BILL_NAME" not in df.columns:
        return df
    pattern = "|".join(keywords)
    return df[df["BILL_NAME"].fillna("").str.contains(pattern, na=False, regex=True)]


def node_search_bills(state: IssueState) -> IssueState:
    df = _load_bills()
    since = date.today() - timedelta(weeks=state["weeks"])
    if "PROPOSE_DT" in df.columns:
        df = df[pd.to_datetime(df["PROPOSE_DT"], errors="coerce").dt.date >= since]
    matches = _match(df, state["keywords"])
    records = matches.head(100).to_dict(orient="records")
    log.info("new bills matched", count=len(records))
    return {"new_bills": records}


def node_law_changes(state: IssueState) -> IssueState:
    """Placeholder: detect laws with enforcement_date within window."""
    laws_dir = settings.raw_dir / "laws"
    changes: list[dict[str, Any]] = []
    if not laws_dir.exists():
        return {"law_changes": changes}

    cutoff = date.today() - timedelta(weeks=state["weeks"])
    for law_dir in laws_dir.iterdir():
        for js in law_dir.glob("*.json"):
            import json

            data = json.loads(js.read_text(encoding="utf-8"))
            enforce_raw = data.get("enforcement_date") or ""
            try:
                enforce = datetime.strptime(enforce_raw, "%Y%m%d").date()
            except ValueError:
                continue
            if enforce >= cutoff and any(k in data.get("law_name", "") for k in state["keywords"]):
                changes.append(
                    {
                        "law_name": data.get("law_name"),
                        "enforcement_date": enforce_raw,
                        "ministry": data.get("ministry"),
                    }
                )
    return {"law_changes": changes}


def node_member_activity(state: IssueState) -> IssueState:
    bills = state.get("new_bills", [])
    counter: Counter[str] = Counter()
    for b in bills:
        proposer = (b.get("RST_PROPOSER") or "").strip()
        if proposer:
            counter[proposer] += 1
    top = [{"name": n, "bills_count": c} for n, c in counter.most_common(10)]
    return {"member_activity": top}


def node_trend(state: IssueState) -> IssueState:
    bills = state.get("new_bills", [])
    by_week: dict[str, list[str]] = {}
    for b in bills:
        dt = str(b.get("PROPOSE_DT", ""))
        try:
            d = datetime.fromisoformat(dt).date()
        except ValueError:
            continue
        iso = f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"
        by_week.setdefault(iso, []).append(b.get("BILL_NAME", ""))
    trend = [
        TrendBucket(
            period=period,
            bill_count=len(names),
            keywords=[k for k in state["keywords"] if any(k in n for n in names)],
        )
        for period, names in sorted(by_week.items())
    ]
    return {"trend": trend}


def node_alert(state: IssueState) -> IssueState:
    n_bills = len(state.get("new_bills", []))
    n_changes = len(state.get("law_changes", []))
    if n_bills >= 10 or n_changes >= 3:
        alert = IssueAlert(
            level="high",
            reason=f"{state['weeks']}주간 신규 의안 {n_bills}건 / 법령 변경 {n_changes}건",
            recommendation="GR 주간 회의 안건 상정 권고",
        )
    elif n_bills >= 3:
        alert = IssueAlert(
            level="medium",
            reason=f"신규 의안 {n_bills}건",
            recommendation="담당자 모니터링",
        )
    else:
        alert = IssueAlert(
            level="info",
            reason="유의미한 변화 없음",
            recommendation="정기 모니터링 유지",
        )
    return {"alert": alert}


def node_compose(state: IssueState) -> IssueState:
    llm = get_llm(fast=True)
    prompt = f"""아래 데이터로 GR팀 주간 쟁점 리포트를 마크다운으로 작성하라.
섹션: 요약 / 신규 의안 Top5 / 법령 변경 / 활동 의원 Top5 / 추세 / 권고.
팩트만, 정치적 평가 금지, 한국어.

키워드: {state['keywords']}
기간: 최근 {state['weeks']}주

신규 의안 ({len(state.get('new_bills', []))}건):
{state.get('new_bills', [])[:10]}

법령 변경 ({len(state.get('law_changes', []))}건):
{state.get('law_changes', [])}

활동 의원:
{state.get('member_activity', [])}

추세:
{[b.model_dump() for b in state.get('trend', [])]}

알림: {state.get('alert').model_dump() if state.get('alert') else {}}
"""
    resp = llm.invoke([{"role": "user", "content": prompt}])
    md = resp.content if isinstance(resp.content, str) else str(resp.content)

    report = IssueReport(
        keywords=state["keywords"],
        weeks=state["weeks"],
        new_bills=state.get("new_bills", []),
        law_changes=state.get("law_changes", []),
        member_activity=state.get("member_activity", []),
        trend=state.get("trend", []),
        alert=state.get("alert"),
        summary_markdown=md,
    )
    return {"report": report}


def build_graph():
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph

    graph = StateGraph(IssueState)
    graph.add_node("bills", node_search_bills)
    graph.add_node("law_changes", node_law_changes)
    graph.add_node("members", node_member_activity)
    graph.add_node("trend", node_trend)
    graph.add_node("alert", node_alert)
    graph.add_node("compose", node_compose)
    graph.set_entry_point("bills")
    graph.add_edge("bills", "law_changes")
    graph.add_edge("law_changes", "members")
    graph.add_edge("members", "trend")
    graph.add_edge("trend", "alert")
    graph.add_edge("alert", "compose")
    graph.add_edge("compose", END)
    return graph.compile(checkpointer=MemorySaver())


class IssueTrackerAgent:
    def __init__(self) -> None:
        self.app = build_graph()

    def run(self, keywords: list[str], weeks: int = 1) -> IssueReport:
        state = self.app.invoke(
            {"keywords": keywords, "weeks": weeks},
            config={"configurable": {"thread_id": "_".join(keywords)}},
        )
        return state["report"]


def schedule_weekly(keywords: list[str]) -> None:
    """Run weekly at Monday 09:00 KST."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BlockingScheduler(timezone="Asia/Seoul")
    agent = IssueTrackerAgent()

    def job() -> None:
        report = agent.run(keywords, weeks=1)
        out = settings.logs_dir / f"issue_report_{datetime.now().date().isoformat()}.md"
        out.write_text(report.summary_markdown, encoding="utf-8")
        log.info("weekly report saved", path=str(out))

    sched.add_job(job, CronTrigger(day_of_week="mon", hour=9, minute=0))
    log.info("scheduler started")
    sched.start()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--keywords", nargs="+", default=DEFAULT_KEYWORDS)
    parser.add_argument("--weeks", type=int, default=1)
    parser.add_argument("--schedule", action="store_true", help="Run on weekly schedule")
    args = parser.parse_args()

    if args.schedule:
        schedule_weekly(args.keywords)
    else:
        report = IssueTrackerAgent().run(args.keywords, args.weeks)
        print(report.summary_markdown)


if __name__ == "__main__":
    main()
