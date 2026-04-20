"""MCP server exposing LEGi-AI capabilities to Claude Desktop.

Start with stdio transport (default for Claude Desktop):
    python -m legi_ai.mcp.server

Claude Desktop config snippet:
{
  "mcpServers": {
    "legi-ai": {
      "command": "uv",
      "args": ["--directory", "/path/to/legi-ai", "run", "python", "-m", "legi_ai.mcp.server"]
    }
  }
}
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool

from legi_ai.config import settings
from legi_ai.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)

server: Server = Server("legi-ai")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_bills",
            description="22대 국회 의안을 키워드·기간·위원회로 검색한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "date_from": {"type": "string", "format": "date"},
                    "date_to": {"type": "string", "format": "date"},
                    "committee": {"type": "string"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_bill_analysis",
            description="특정 의안을 LangGraph 에이전트로 심층 분석한다.",
            inputSchema={
                "type": "object",
                "properties": {"bill_id": {"type": "string"}},
                "required": ["bill_id"],
            },
        ),
        Tool(
            name="get_member_profile",
            description="국회의원의 발의 이력·정책영역·네트워크 프로필을 생성한다.",
            inputSchema={
                "type": "object",
                "properties": {"member_id": {"type": "string"}},
                "required": ["member_id"],
            },
        ),
        Tool(
            name="search_laws",
            description="국가법령정보센터 수집 데이터에서 법령·조문을 검색한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "law_type": {"type": "string", "enum": ["law", "article"]},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="track_issue",
            description="키워드 기반 쟁점 추적 주간 리포트를 생성한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "weeks": {"type": "integer", "default": 4},
                },
                "required": ["keywords"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    log.info("tool call", name=name, args=arguments)

    if name == "search_bills":
        from legi_ai.rag.query_engine import query

        filters: dict[str, Any] = {"source_type": "bill"}
        if arguments.get("committee"):
            filters["committee"] = arguments["committee"]
        result = query(arguments["query"], filters=filters)
        return [TextContent(type="text", text=json.dumps(result.model_dump(), ensure_ascii=False, indent=2))]

    if name == "get_bill_analysis":
        from legi_ai.agents.bill_analyst import BillAnalystAgent

        res = BillAnalystAgent().run(arguments["bill_id"])
        return [TextContent(type="text", text=res.get("report_markdown", ""))]

    if name == "get_member_profile":
        from legi_ai.agents.member_profiler import MemberProfilerAgent

        res = MemberProfilerAgent().run(arguments["member_id"])
        profile = res.get("profile")
        if not profile:
            return [TextContent(type="text", text="프로필 생성 실패")]
        return [TextContent(type="text", text=profile.profile_markdown)]

    if name == "search_laws":
        from legi_ai.rag.query_engine import query

        filters = {"source_type": arguments.get("law_type", "article")}
        result = query(arguments["query"], filters=filters)
        return [TextContent(type="text", text=result.answer)]

    if name == "track_issue":
        from legi_ai.agents.issue_tracker import IssueTrackerAgent

        report = IssueTrackerAgent().run(arguments["keywords"], arguments.get("weeks", 4))
        return [TextContent(type="text", text=report.summary_markdown)]

    return [TextContent(type="text", text=f"unknown tool: {name}")]


@server.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(
            uri="legi://status",
            name="LEGi-AI 상태",
            description="현재 인덱스 상태와 설정 요약",
            mimeType="application/json",
        )
    ]


@server.read_resource()
async def read_resource(uri: str) -> str:
    if uri == "legi://status":
        return json.dumps(
            {
                "env": settings.legi_env,
                "qdrant": settings.qdrant_url,
                "model": settings.claude_model_primary,
                "collection": settings.qdrant_collection,
            },
            ensure_ascii=False,
            indent=2,
        )
    return f"unknown resource: {uri}"


async def run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
