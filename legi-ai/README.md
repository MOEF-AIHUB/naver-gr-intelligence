# LEGi-AI

AI-powered legislative intelligence for the Naver GR platform. Provides
RAG-based Q&A over Korean National Assembly bills, members, and statutes,
plus LangGraph agents for bill analysis, member profiling, and issue tracking.

## Quickstart

```bash
# 1. Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install dependencies
cd legi-ai
uv sync --all-extras

# 3. Copy env template and fill keys
cp .env.example .env

# 4. Verify install
uv run pytest tests/test_smoke.py

# 5. Start Qdrant
docker compose up -d qdrant
uv run python scripts/init_qdrant.py

# 6. Ingest data
uv run python -m legi_ai.ingestion.assembly_api --target bills --since 2024-05-30
uv run python -m legi_ai.ingestion.assembly_api --target members
uv run python -m legi_ai.ingestion.law_api --laws 조세특례제한법 소득세법 법인세법

# 7. Normalize and index
uv run python -m legi_ai.ingestion.normalizer
uv run python -m legi_ai.rag.indexer --input data/processed/documents.jsonl

# 8. Query
uv run python -m legi_ai.rag.query_engine "온라인플랫폼 공정화 관련 계류 법안"

# 9. Run API
uv run uvicorn legi_ai.api.main:app --reload
```

## Architecture

```
┌───────────────────────────────────────────────────────────┐
│                      LEGi Frontend (HTML)                  │
│                    /api/v1/chat (SSE stream)               │
└────────────────────────────┬──────────────────────────────┘
                             │
┌────────────────────────────▼──────────────────────────────┐
│           FastAPI + Supervisor Agent (LangGraph)           │
│  ┌────────────┐  ┌────────────┐  ┌───────────────────┐    │
│  │BillAnalyst │  │MemberProfile│ │  IssueTracker     │    │
│  └─────┬──────┘  └─────┬──────┘  └─────────┬─────────┘    │
│        └───────────────┼──────────────────┘               │
│                        ▼                                   │
│        ┌──────────────────────────────┐                   │
│        │   RAG Query Engine           │                   │
│        │  (LlamaIndex + Qdrant + BM25)│                   │
│        └──────────────┬───────────────┘                   │
└────────────────────────────┬──────────────────────────────┘
                             │
┌────────────────────────────▼──────────────────────────────┐
│       Ingestion: Assembly API, Law API, Normalizer         │
└───────────────────────────────────────────────────────────┘
```

## Modules

- `legi_ai.ingestion` — Fetch bills, members, and statutes from Korean gov APIs
- `legi_ai.rag` — Hybrid retrieval (vector + BM25), rerank, cited answer synthesis
- `legi_ai.agents` — Bill analyst, member profiler, issue tracker, supervisor
- `legi_ai.evaluation` — Ragas-based offline eval against a golden set
- `legi_ai.mcp` — MCP server for Claude Desktop integration
- `legi_ai.api` — FastAPI app for the LEGi frontend

## Development

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```
