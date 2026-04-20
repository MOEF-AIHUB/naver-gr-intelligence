"""Hybrid retrieval (vector + BM25) + rerank + cited answer synthesis.

Usage:
    python -m legi_ai.rag.query_engine "온라인플랫폼 공정화 관련 계류 법안"
"""
from __future__ import annotations

import argparse
import time
from typing import Any

from pydantic import BaseModel, Field

from legi_ai.config import settings
from legi_ai.logging import configure_logging, get_logger
from legi_ai.rag.cache import cached, default_ttl_resolver
from legi_ai.rag.query_logger import logged

log = get_logger(__name__)


class Source(BaseModel):
    doc_id: str
    title: str
    source_type: str
    score: float
    snippet: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class QueryResult(BaseModel):
    question: str
    answer: str
    sources: list[Source]
    confidence: float
    latency_ms: int
    model: str


SYSTEM_PROMPT = """당신은 대한민국 국회·법령 데이터를 분석하는 전문가 AI입니다.
네이버 GR(Government Relations) 팀의 LEGi 플랫폼에서 동작합니다.

답변 원칙:
1. 반드시 제공된 CONTEXT만 근거로 답변하세요.
2. CONTEXT에 없는 사실은 추측하지 말고 "제공된 자료에서 확인되지 않습니다"라고 답하세요.
3. 모든 주장에 [출처 번호]를 붙이세요 (예: [1], [2]).
4. 법령 조문을 인용할 때는 "○○법 제N조"와 같이 명시하세요.
5. 정치적 평가나 당파적 해석은 배제하고 사실 기반으로 서술하세요.
6. 답변은 한국어로, 간결하고 구조화된 형식(불릿·표)으로 작성하세요.
"""


def _get_index():
    from llama_index.core import VectorStoreIndex
    from llama_index.core.settings import Settings as LISettings
    from llama_index.embeddings.voyageai import VoyageEmbedding
    from llama_index.vector_stores.qdrant import QdrantVectorStore
    from qdrant_client import QdrantClient

    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    store = QdrantVectorStore(client=client, collection_name=settings.qdrant_collection)

    if settings.voyage_api_key:
        LISettings.embed_model = VoyageEmbedding(
            model_name=settings.embedding_model,
            voyage_api_key=settings.voyage_api_key,
        )
    else:
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding

        LISettings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-m3")

    return VectorStoreIndex.from_vector_store(store)


def _hybrid_retriever(index, top_k: int = 10, filters: dict | None = None):
    from llama_index.core.retrievers import QueryFusionRetriever
    from llama_index.retrievers.bm25 import BM25Retriever

    vector_retriever = index.as_retriever(similarity_top_k=top_k, filters=_to_filters(filters))
    docstore = index.docstore
    bm25_retriever = BM25Retriever.from_defaults(
        docstore=docstore,
        similarity_top_k=top_k,
    )
    fusion = QueryFusionRetriever(
        retrievers=[vector_retriever, bm25_retriever],
        similarity_top_k=top_k,
        mode="reciprocal_rerank",
        num_queries=1,
        use_async=False,
    )
    return fusion


def _to_filters(filters: dict | None):
    if not filters:
        return None
    from llama_index.core.vector_stores import FilterCondition, MetadataFilter, MetadataFilters

    entries: list[MetadataFilter] = []
    for key, value in filters.items():
        if value is None:
            continue
        entries.append(MetadataFilter(key=key, value=value))
    if not entries:
        return None
    return MetadataFilters(filters=entries, condition=FilterCondition.AND)


def _rerank(nodes, query: str, top_n: int = 5):
    if settings.cohere_api_key:
        from llama_index.postprocessor.cohere_rerank import CohereRerank

        reranker = CohereRerank(api_key=settings.cohere_api_key, top_n=top_n, model="rerank-multilingual-v3.0")
        return reranker.postprocess_nodes(nodes, query_str=query)

    from llama_index.core.postprocessor import LLMRerank
    from llama_index.llms.anthropic import Anthropic

    llm = Anthropic(model=settings.claude_model_fast, api_key=settings.anthropic_api_key)
    reranker = LLMRerank(llm=llm, top_n=top_n)
    return reranker.postprocess_nodes(nodes, query_str=query)


def _format_context(nodes) -> tuple[str, list[Source]]:
    sources: list[Source] = []
    blocks: list[str] = []
    for i, n in enumerate(nodes, start=1):
        meta = n.metadata or {}
        title = meta.get("title", "untitled")
        source_type = meta.get("source_type", "unknown")
        doc_id = meta.get("doc_id", n.node_id)
        text = n.get_content()
        blocks.append(f"[{i}] ({source_type}) {title}\n{text}")
        sources.append(
            Source(
                doc_id=doc_id,
                title=title,
                source_type=source_type,
                score=float(getattr(n, "score", 0.0) or 0.0),
                snippet=text[:400],
                metadata=meta,
            )
        )
    return "\n\n---\n\n".join(blocks), sources


def _synthesize(question: str, context: str) -> tuple[str, float]:
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    resp = client.messages.create(
        model=settings.claude_model_primary,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"질문: {question}\n\nCONTEXT:\n{context}\n\n위 CONTEXT만을 근거로 질문에 답하고, 각 주장에 [출처 번호]를 붙여주세요.",
            }
        ],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    confidence = 0.9 if "확인되지 않습니다" not in text else 0.4
    return text, confidence


@cached(ttl_resolver=default_ttl_resolver)
@logged
def query(
    question: str,
    *,
    top_k: int = 10,
    top_n: int = 5,
    filters: dict | None = None,
) -> QueryResult:
    start = time.perf_counter()
    index = _get_index()
    retriever = _hybrid_retriever(index, top_k=top_k, filters=filters)
    raw_nodes = retriever.retrieve(question)
    ranked = _rerank(raw_nodes, question, top_n=top_n)
    context, sources = _format_context(ranked)
    answer, confidence = _synthesize(question, context)
    latency_ms = int((time.perf_counter() - start) * 1000)

    result = QueryResult(
        question=question,
        answer=answer,
        sources=sources,
        confidence=confidence,
        latency_ms=latency_ms,
        model=settings.claude_model_primary,
    )
    log.info("query complete", latency_ms=latency_ms, sources=len(sources))
    return result


SAMPLE_QUERIES = [
    "조세특례제한법상 경력단절자 재취업 세액공제 요건은?",
    "22대 국회에서 AI 관련 의안 중 소관위 심사 중인 것",
    "국유재산 창업공간 전환 관련 법령 근거",
    "온라인플랫폼 공정화 관련 계류 법안과 대표발의자는?",
    "벤처투자 촉진법에서 개인투자조합의 요건",
]


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="LEGi-AI RAG query engine")
    parser.add_argument("question", nargs="?", default=None)
    parser.add_argument("--samples", action="store_true", help="Run the 5 sample queries")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--top-n", type=int, default=5)
    args = parser.parse_args()

    if args.samples:
        for q in SAMPLE_QUERIES:
            print(f"\n===== {q} =====")
            r = query(q, top_k=args.top_k, top_n=args.top_n)
            print(r.answer)
            print(f"\n(confidence={r.confidence}, latency={r.latency_ms}ms)")
    elif args.question:
        r = query(args.question, top_k=args.top_k, top_n=args.top_n)
        print(r.model_dump_json(indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
