"""LangGraph agent for profiling a National Assembly member.

Usage:
    python -m legi_ai.agents.member_profiler --member-id <MONA_CD>
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any, TypedDict

import pandas as pd
from pydantic import BaseModel, Field

from legi_ai.agents.common import get_llm, structured_call
from legi_ai.config import settings
from legi_ai.logging import configure_logging, get_logger

log = get_logger(__name__)


class PolicyAreas(BaseModel):
    primary_areas: list[str] = Field(description="주요 정책 영역 (상위 3-5개)")
    sub_topics: dict[str, list[str]] = Field(description="영역별 세부 주제")


class VotingPattern(BaseModel):
    stance_summary: str = Field(description="표결 경향 요약 (당론 추종 여부 등 사실 기반)")
    notable_defections: list[str] = Field(description="당론과 다른 주목할 표결")


class MemberProfile(BaseModel):
    name: str
    party: str
    district: str
    committee: str
    bills_proposed_count: int
    policy_areas: PolicyAreas
    voting_pattern: VotingPattern
    top_cosponsors: list[str]
    profile_markdown: str


class MemberState(TypedDict, total=False):
    member_id: str
    member_info: dict[str, Any]
    bills: list[dict[str, Any]]
    votes: list[dict[str, Any]]
    policy_areas: PolicyAreas
    voting_pattern: VotingPattern
    network: dict[str, Any]
    profile: MemberProfile


def _load_member_info(member_id: str) -> dict[str, Any]:
    members_dir = settings.raw_dir / "members"
    if not members_dir.exists():
        return {"MONA_CD": member_id}
    latest = sorted(members_dir.glob("*.parquet"))[-1]
    df = pd.read_parquet(latest)
    rows = df[df["MONA_CD"] == member_id].to_dict(orient="records")
    return rows[0] if rows else {"MONA_CD": member_id}


def _load_member_bills(member_name: str) -> list[dict[str, Any]]:
    bills_dir = settings.raw_dir / "bills"
    if not bills_dir.exists():
        return []
    latest = sorted(bills_dir.glob("*.parquet"))[-1]
    df = pd.read_parquet(latest)
    if "RST_PROPOSER" not in df.columns:
        return []
    mask = df["RST_PROPOSER"].fillna("").str.contains(member_name, na=False)
    return df[mask].to_dict(orient="records")


def node_fetch(state: MemberState) -> MemberState:
    info = _load_member_info(state["member_id"])
    bills = _load_member_bills(info.get("HG_NM", ""))
    return {"member_info": info, "bills": bills, "votes": []}


def node_policy_areas(state: MemberState) -> MemberState:
    titles = [b.get("BILL_NAME", "") for b in state.get("bills", [])][:50]
    if not titles:
        return {
            "policy_areas": PolicyAreas(primary_areas=[], sub_topics={}),
        }
    result = structured_call(
        PolicyAreas,
        system="의원의 발의 의안 제목들을 분석하여 정책 관심 영역을 사실 기반으로 분류하라.",
        user="발의 의안 제목:\n" + "\n".join(f"- {t}" for t in titles),
    )
    return {"policy_areas": result}


def node_voting(state: MemberState) -> MemberState:
    votes = state.get("votes") or []
    if not votes:
        return {
            "voting_pattern": VotingPattern(
                stance_summary="표결 데이터 미수집 — 향후 votes 수집 파이프라인 연동 필요",
                notable_defections=[],
            )
        }
    result = structured_call(
        VotingPattern,
        system="표결 이력에서 사실적 패턴만 추출하라. 정치적 평가 금지.",
        user=f"표결 {len(votes)}건",
    )
    return {"voting_pattern": result}


def node_network(state: MemberState) -> MemberState:
    import networkx as nx

    bills = state.get("bills", [])
    g = nx.Graph()
    name = state.get("member_info", {}).get("HG_NM", "")
    g.add_node(name)
    cosponsors: Counter[str] = Counter()
    for b in bills:
        others = (b.get("PUBL_PROPOSER") or "").split(",")
        for o in [o.strip() for o in others if o.strip() and o.strip() != name]:
            cosponsors[o] += 1
            g.add_edge(name, o, weight=cosponsors[o])

    out_dir = settings.processed_dir / "networks"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{state['member_id']}.graphml"
    nx.write_graphml(g, path)
    log.info("network saved", path=str(path), nodes=g.number_of_nodes(), edges=g.number_of_edges())

    return {"network": {"path": str(path), "top_cosponsors": cosponsors.most_common(10)}}


def node_profile(state: MemberState) -> MemberState:
    info = state.get("member_info", {})
    bills = state.get("bills", [])
    pa = state["policy_areas"]
    vp = state["voting_pattern"]
    net = state.get("network", {})
    top = [name for name, _ in net.get("top_cosponsors", [])]

    llm = get_llm()
    prompt = f"""아래 의원 분석 결과로 LEGi 대시보드 카드용 프로필 마크다운을 작성하라.
정치적 평가 금지, 사실만, 한국어.

- 이름: {info.get('HG_NM')}
- 정당: {info.get('POLY_NM')}
- 선거구: {info.get('ORIG_NM')}
- 위원회: {info.get('CMIT_NM')}
- 발의 건수: {len(bills)}
- 정책영역: {pa.model_dump_json()}
- 표결경향: {vp.model_dump_json()}
- 주요 공동발의자: {top}
"""
    resp = llm.invoke([{"role": "user", "content": prompt}])
    md = resp.content if isinstance(resp.content, str) else str(resp.content)

    profile = MemberProfile(
        name=info.get("HG_NM", ""),
        party=info.get("POLY_NM", "") or "",
        district=info.get("ORIG_NM", "") or "",
        committee=info.get("CMIT_NM", "") or "",
        bills_proposed_count=len(bills),
        policy_areas=pa,
        voting_pattern=vp,
        top_cosponsors=top,
        profile_markdown=md,
    )
    return {"profile": profile}


def build_graph():
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph

    graph = StateGraph(MemberState)
    graph.add_node("fetch", node_fetch)
    graph.add_node("policy", node_policy_areas)
    graph.add_node("voting", node_voting)
    graph.add_node("network", node_network)
    graph.add_node("profile", node_profile)
    graph.set_entry_point("fetch")
    graph.add_edge("fetch", "policy")
    graph.add_edge("policy", "voting")
    graph.add_edge("voting", "network")
    graph.add_edge("network", "profile")
    graph.add_edge("profile", END)
    return graph.compile(checkpointer=MemorySaver())


class MemberProfilerAgent:
    def __init__(self) -> None:
        self.app = build_graph()

    def run(self, member_id: str) -> MemberState:
        return self.app.invoke(
            {"member_id": member_id},
            config={"configurable": {"thread_id": member_id}},
        )


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--member-id", required=True)
    args = parser.parse_args()
    result = MemberProfilerAgent().run(args.member_id)
    profile = result.get("profile")
    if profile:
        print(profile.profile_markdown)


if __name__ == "__main__":
    main()
