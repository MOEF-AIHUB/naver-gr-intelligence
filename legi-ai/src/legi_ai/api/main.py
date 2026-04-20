"""FastAPI application exposing the supervisor agent and RAG engine."""
from __future__ import annotations

import json
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from legi_ai.agents.bill_analyst import BillAnalystAgent
from legi_ai.agents.issue_tracker import IssueTrackerAgent
from legi_ai.agents.member_profiler import MemberProfilerAgent
from legi_ai.agents.supervisor import SupervisorAgent
from legi_ai.config import settings
from legi_ai.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)

app = FastAPI(title="LEGi-AI", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


supervisor = SupervisorAgent()
bill_agent = BillAnalystAgent()
member_agent = MemberProfilerAgent()
issue_agent = IssueTrackerAgent()


class ChatRequest(BaseModel):
    query: str
    thread_id: str = "default"


class IssueRequest(BaseModel):
    keywords: list[str]
    weeks: int = 4


def _verify_api_key(x_legi_key: str = Header(default="")) -> None:
    if settings.legi_env == "production" and not x_legi_key:
        raise HTTPException(status_code=401, detail="missing x-legi-key")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "env": settings.legi_env, "model": settings.claude_model_primary}


@app.post("/api/v1/chat")
async def chat(req: ChatRequest, _: None = Depends(_verify_api_key)) -> dict:
    answer = supervisor.run(req.query, thread_id=req.thread_id)
    return {"answer": answer, "thread_id": req.thread_id}


@app.post("/api/v1/chat/stream")
async def chat_stream(req: ChatRequest, _: None = Depends(_verify_api_key)):
    async def gen() -> AsyncGenerator[dict, None]:
        for event in supervisor.stream(req.query, thread_id=req.thread_id):
            yield {"event": "step", "data": json.dumps(event, default=str, ensure_ascii=False)}
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(gen())


@app.get("/api/v1/bills/{bill_id}/analyze")
def analyze_bill(bill_id: str, _: None = Depends(_verify_api_key)) -> dict:
    result = bill_agent.run(bill_id)
    return {
        "bill_id": bill_id,
        "report_markdown": result.get("report_markdown", ""),
        "key_points": result["key_points"].model_dump() if result.get("key_points") else None,
    }


@app.get("/api/v1/members/{member_id}/profile")
def member_profile(member_id: str, _: None = Depends(_verify_api_key)) -> dict:
    result = member_agent.run(member_id)
    profile = result.get("profile")
    if not profile:
        raise HTTPException(status_code=404, detail="profile build failed")
    return profile.model_dump()


@app.post("/api/v1/issues/track")
def track_issue(req: IssueRequest, _: None = Depends(_verify_api_key)) -> dict:
    report = issue_agent.run(req.keywords, req.weeks)
    return report.model_dump()
