"""LangGraph agent for deep analysis of a single bill.

Usage:
    python -m legi_ai.agents.bill_analyst --bill-id PRC_...
"""
from __future__ import annotations

import argparse
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

from legi_ai.agents.common import structured_call
from legi_ai.logging import configure_logging, get_logger

log = get_logger(__name__)


class KeyPoints(BaseModel):
    summary: str = Field(description="1-2문장 핵심 요약")
    purpose: str = Field(description="입법 목적")
    key_provisions: list[str] = Field(description="주요 조항")
    affected_sectors: list[str] = Field(description="영향 받는 산업/섹터")


class Stakeholder(BaseModel):
    name: str
    role: str
    expected_position: Literal["찬성", "반대", "중립", "불명"]
    rationale: str


class Stakeholders(BaseModel):
    stakeholders: list[Stakeholder]


class ImpactDimension(BaseModel):
    dimension: str
    direction: Literal["positive", "negative", "neutral", "mixed"]
    magnitude: Literal["high", "medium", "low"]
    description: str


class ImpactAnalysis(BaseModel):
    dimensions: list[ImpactDimension]
    naver_specific_impact: str = Field(description="네이버 사업에의 구체적 영향")
    overall_severity: Literal["critical", "high", "medium", "low"]


class Risk(BaseModel):
    category: Literal["regulatory", "reputational", "operational", "legal", "financial"]
    description: str
    likelihood: Literal["high", "medium", "low"]
    mitigation: str


class Risks(BaseModel):
    risks: list[Risk]


class BillState(TypedDict, total=False):
    bill_id: str
    raw_text: str
    key_points: KeyPoints
    related_laws: list[dict[str, Any]]
    stakeholders: list[Stakeholder]
    impact: ImpactAnalysis
    risks: list[Risk]
    recommendation: str
    report_markdown: str


def _load_bill(bill_id: str) -> str:
    """Fetch bill content from RAG store. Falls back to bill-id-only context."""
    try:
        from legi_ai.rag.query_engine import query

        res = query(f"의안 ID {bill_id}의 전체 내용과 제안이유, 주요내용", top_k=8, top_n=3)
        return res.answer + "\n\n" + "\n".join(s.snippet for s in res.sources)
    except Exception as exc:
        log.warning("bill load fallback", error=str(exc))
        return f"(RAG 미가용. 의안 ID: {bill_id})"


def _find_related_laws(text: str) -> list[dict[str, Any]]:
    try:
        from legi_ai.rag.query_engine import query

        res = query(
            f"다음 의안과 관련된 현행 법령 조문을 찾아주세요:\n{text[:1500]}",
            top_k=10,
            top_n=5,
            filters={"source_type": "article"},
        )
        return [s.model_dump() for s in res.sources]
    except Exception as exc:
        log.warning("related laws fallback", error=str(exc))
        return []


def node_extract_key_points(state: BillState) -> BillState:
    text = state.get("raw_text", "") or _load_bill(state["bill_id"])
    kp = structured_call(
        KeyPoints,
        system="너는 입법 분석 전문가다. 의안에서 핵심 정보만 뽑아 구조화해라.",
        user=text,
    )
    return {"raw_text": text, "key_points": kp}


def node_related_laws(state: BillState) -> BillState:
    laws = _find_related_laws(state["raw_text"])
    return {"related_laws": laws}


def node_stakeholders(state: BillState) -> BillState:
    kp = state["key_points"]
    result = structured_call(
        Stakeholders,
        system="의안의 이해관계자를 식별하고 예상 포지션을 분석하라. 사실 기반으로만.",
        user=f"요약: {kp.summary}\n목적: {kp.purpose}\n영향 섹터: {', '.join(kp.affected_sectors)}",
    )
    return {"stakeholders": result.stakeholders}


def node_impact(state: BillState) -> BillState:
    kp = state["key_points"]
    laws_ctx = "\n".join(f"- {l.get('title')}: {l.get('snippet', '')[:200]}" for l in state.get("related_laws", []))
    result = structured_call(
        ImpactAnalysis,
        system=(
            "너는 네이버 GR팀 소속 입법 영향 분석가다. "
            "해당 의안이 네이버 사업(검색, 쇼핑, 웹툰, 클라우드, AI, 페이)에 미치는 "
            "구체적 영향을 평가하라."
        ),
        user=f"의안 요약: {kp.summary}\n주요 조항: {kp.key_provisions}\n관련 법령:\n{laws_ctx}",
    )
    return {"impact": result}


def node_risks(state: BillState) -> BillState:
    impact = state["impact"]
    result = structured_call(
        Risks,
        system="입법 리스크를 범주별로 평가하고 완화 방안을 제시하라.",
        user=f"영향 분석: {impact.model_dump_json()}",
    )
    return {"risks": result.risks}


def node_synthesize(state: BillState) -> BillState:
    from legi_ai.agents.common import get_llm

    llm = get_llm()
    kp = state["key_points"]
    impact = state["impact"]
    risks = state.get("risks", [])
    sh = state.get("stakeholders", [])

    prompt = f"""아래 분석 결과를 바탕으로 네이버 GR 임원용 1-page 브리핑을 작성해주세요.
마크다운 형식, 한국어, 팩트 기반, 평가적 수사 금지.

## 의안
- ID: {state['bill_id']}

## 핵심 요약
{kp.model_dump_json(indent=2)}

## 이해관계자
{[s.model_dump() for s in sh]}

## 영향 분석
{impact.model_dump_json(indent=2)}

## 리스크
{[r.model_dump() for r in risks]}

브리핑 섹션: 요약 / 주요 조항 / 네이버 영향 / 이해관계자 / 리스크와 대응 / 권고 액션
"""
    resp = llm.invoke([{"role": "user", "content": prompt}])
    report = resp.content if isinstance(resp.content, str) else str(resp.content)
    return {"report_markdown": report, "recommendation": report[-500:]}


def should_skip_risks(state: BillState) -> str:
    impact = state.get("impact")
    if impact and impact.overall_severity == "low":
        return "synthesize"
    return "risks"


def build_graph():
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph

    graph = StateGraph(BillState)
    graph.add_node("extract", node_extract_key_points)
    graph.add_node("related_laws", node_related_laws)
    graph.add_node("stakeholders", node_stakeholders)
    graph.add_node("impact", node_impact)
    graph.add_node("risks", node_risks)
    graph.add_node("synthesize", node_synthesize)

    graph.set_entry_point("extract")
    graph.add_edge("extract", "related_laws")
    graph.add_edge("related_laws", "stakeholders")
    graph.add_edge("stakeholders", "impact")
    graph.add_conditional_edges("impact", should_skip_risks, {"risks": "risks", "synthesize": "synthesize"})
    graph.add_edge("risks", "synthesize")
    graph.add_edge("synthesize", END)

    return graph.compile(checkpointer=MemorySaver())


class BillAnalystAgent:
    def __init__(self) -> None:
        self.app = build_graph()

    def run(self, bill_id: str, thread_id: str | None = None) -> BillState:
        config = {"configurable": {"thread_id": thread_id or bill_id}}
        final = self.app.invoke({"bill_id": bill_id}, config=config)
        return final


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--bill-id", required=True)
    args = parser.parse_args()
    agent = BillAnalystAgent()
    result = agent.run(args.bill_id)
    print(result.get("report_markdown", ""))


if __name__ == "__main__":
    main()
