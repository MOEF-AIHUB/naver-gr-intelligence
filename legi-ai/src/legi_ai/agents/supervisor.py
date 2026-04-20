"""Supervisor agent routing natural-language queries to the right sub-agent."""
from __future__ import annotations

import argparse
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

from legi_ai.agents.bill_analyst import BillAnalystAgent
from legi_ai.agents.common import get_llm, structured_call
from legi_ai.agents.issue_tracker import IssueTrackerAgent
from legi_ai.agents.member_profiler import MemberProfilerAgent
from legi_ai.logging import configure_logging, get_logger

log = get_logger(__name__)

Intent = Literal["bill_analysis", "member_analysis", "issue_tracking", "general_qa"]


class RoutingDecision(BaseModel):
    intent: Intent
    bill_id: str | None = Field(default=None)
    member_id: str | None = None
    member_name: str | None = None
    keywords: list[str] = Field(default_factory=list)
    weeks: int = 4
    needs_followup: bool = False


class SupervisorState(TypedDict, total=False):
    query: str
    history: list[dict[str, Any]]
    decision: RoutingDecision
    partial_results: list[dict[str, Any]]
    final_answer: str


ROUTER_SYSTEM = """너는 네이버 GR LEGi 플랫폼의 라우터 AI다.
사용자 질의를 분석해 아래 intent 중 하나로 분류하고 필요한 파라미터를 추출하라.

- bill_analysis: 특정 의안 심층 분석 (bill_id 필요)
- member_analysis: 특정 의원 프로필 (member_id 또는 member_name)
- issue_tracking: 쟁점/키워드 모니터링 (keywords, weeks)
- general_qa: 단순 질의응답, RAG로 충분한 경우

복합 질의라면 주 intent를 선택하고 needs_followup=True.
"""


def node_route(state: SupervisorState) -> SupervisorState:
    decision = structured_call(
        RoutingDecision,
        system=ROUTER_SYSTEM,
        user=state["query"],
        fast=True,
    )
    log.info("routed", intent=decision.intent, needs_followup=decision.needs_followup)
    return {"decision": decision}


def node_dispatch(state: SupervisorState) -> SupervisorState:
    decision = state["decision"]
    partials = list(state.get("partial_results", []))

    if decision.intent == "bill_analysis" and decision.bill_id:
        result = BillAnalystAgent().run(decision.bill_id)
        partials.append({"kind": "bill_analysis", "markdown": result.get("report_markdown", "")})
    elif decision.intent == "member_analysis":
        mid = decision.member_id or _resolve_member_name(decision.member_name)
        if mid:
            result = MemberProfilerAgent().run(mid)
            profile = result.get("profile")
            if profile:
                partials.append(
                    {"kind": "member_analysis", "markdown": profile.profile_markdown}
                )
    elif decision.intent == "issue_tracking":
        report = IssueTrackerAgent().run(decision.keywords, decision.weeks)
        partials.append({"kind": "issue_tracking", "markdown": report.summary_markdown})
    else:
        from legi_ai.rag.query_engine import query

        res = query(state["query"])
        partials.append(
            {
                "kind": "general_qa",
                "markdown": res.answer,
                "sources": [s.model_dump() for s in res.sources],
            }
        )

    return {"partial_results": partials}


def _resolve_member_name(name: str | None) -> str | None:
    if not name:
        return None
    import pandas as pd

    from legi_ai.config import settings as cfg

    members_dir = cfg.raw_dir / "members"
    if not members_dir.exists():
        return None
    latest = sorted(members_dir.glob("*.parquet"))[-1]
    df = pd.read_parquet(latest)
    rows = df[df["HG_NM"].fillna("").str.contains(name, na=False)]
    if rows.empty:
        return None
    return str(rows.iloc[0]["MONA_CD"])


def node_synthesize(state: SupervisorState) -> SupervisorState:
    partials = state.get("partial_results", [])
    if len(partials) == 1 and not state["decision"].needs_followup:
        return {"final_answer": partials[0].get("markdown", "")}

    llm = get_llm()
    blocks = "\n\n---\n\n".join(
        f"### {p['kind']}\n{p.get('markdown', '')}" for p in partials
    )
    prompt = f"""사용자 질의: {state['query']}

아래 부분 결과를 통합해 질의에 맞는 최종 답변을 한국어 마크다운으로 작성하라.
중복 제거, 핵심 강조, 인용은 그대로 유지하라.

{blocks}
"""
    resp = llm.invoke([{"role": "user", "content": prompt}])
    return {"final_answer": resp.content if isinstance(resp.content, str) else str(resp.content)}


def build_graph():
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph

    graph = StateGraph(SupervisorState)
    graph.add_node("route", node_route)
    graph.add_node("dispatch", node_dispatch)
    graph.add_node("synthesize", node_synthesize)
    graph.set_entry_point("route")
    graph.add_edge("route", "dispatch")
    graph.add_edge("dispatch", "synthesize")
    graph.add_edge("synthesize", END)
    return graph.compile(checkpointer=MemorySaver())


class SupervisorAgent:
    def __init__(self) -> None:
        self.app = build_graph()

    def run(self, query: str, thread_id: str = "default") -> str:
        state = self.app.invoke(
            {"query": query},
            config={"configurable": {"thread_id": thread_id}},
        )
        return state.get("final_answer", "")

    def stream(self, query: str, thread_id: str = "default"):
        for event in self.app.stream(
            {"query": query},
            config={"configurable": {"thread_id": thread_id}},
        ):
            yield event


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    args = parser.parse_args()
    agent = SupervisorAgent()
    print(agent.run(args.query))


if __name__ == "__main__":
    main()
