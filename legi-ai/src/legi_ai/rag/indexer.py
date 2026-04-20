"""Embed normalized documents and upsert them into Qdrant.

Usage:
    python -m legi_ai.rag.indexer --input data/processed/documents.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from tqdm import tqdm

from legi_ai.config import settings
from legi_ai.ingestion.normalizer import Document
from legi_ai.logging import configure_logging, get_logger

log = get_logger(__name__)

BILL_CHUNK_SIZE = 512
BILL_CHUNK_OVERLAP = 64


def _load_docs(path: Path) -> Iterable[Document]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield Document.model_validate(json.loads(line))


def _estimate_cost(docs: list[Document]) -> tuple[int, float]:
    total_chars = sum(len(d.content) for d in docs)
    approx_tokens = total_chars // 2
    voyage_cost = approx_tokens / 1_000_000 * 0.06
    return approx_tokens, voyage_cost


def _build_nodes(docs: list[Document]):
    """Chunk per source_type, preserving law articles as single nodes."""
    from llama_index.core.node_parser import SentenceSplitter
    from llama_index.core.schema import TextNode

    splitter = SentenceSplitter(
        chunk_size=BILL_CHUNK_SIZE,
        chunk_overlap=BILL_CHUNK_OVERLAP,
    )

    nodes = []
    for doc in docs:
        base_meta = {
            "doc_id": doc.doc_id,
            "title": doc.title,
            "source_type": doc.source_type,
            "valid_from": doc.valid_from,
            **doc.metadata,
        }
        if doc.source_type in ("article", "member", "law"):
            nodes.append(
                TextNode(
                    id_=doc.doc_id,
                    text=doc.content,
                    metadata=base_meta,
                )
            )
        else:
            for i, chunk in enumerate(splitter.split_text(doc.content)):
                nodes.append(
                    TextNode(
                        id_=f"{doc.doc_id}_{i}",
                        text=chunk,
                        metadata={**base_meta, "chunk_index": i},
                    )
                )
    return nodes


def _embedding_model():
    from llama_index.embeddings.voyageai import VoyageEmbedding

    if settings.voyage_api_key:
        return VoyageEmbedding(
            model_name=settings.embedding_model,
            voyage_api_key=settings.voyage_api_key,
        )

    log.warning("VOYAGE_API_KEY missing; falling back to local BGE-M3")
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding

    return HuggingFaceEmbedding(model_name="BAAI/bge-m3", trust_remote_code=True)


def _vector_store():
    from llama_index.vector_stores.qdrant import QdrantVectorStore
    from qdrant_client import QdrantClient

    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    return QdrantVectorStore(client=client, collection_name=settings.qdrant_collection)


def index_documents(input_path: Path, batch_size: int = 100) -> int:
    from llama_index.core import StorageContext, VectorStoreIndex
    from llama_index.core.settings import Settings as LISettings

    docs = list(_load_docs(input_path))
    log.info("loaded documents", count=len(docs))

    tokens, cost = _estimate_cost(docs)
    log.info("estimated cost", tokens=tokens, voyage_usd=round(cost, 4))

    nodes = _build_nodes(docs)
    log.info("built nodes", count=len(nodes))

    LISettings.embed_model = _embedding_model()
    storage = StorageContext.from_defaults(vector_store=_vector_store())

    total = 0
    for i in tqdm(range(0, len(nodes), batch_size), desc="indexing"):
        batch = nodes[i : i + batch_size]
        VectorStoreIndex(
            nodes=batch,
            storage_context=storage,
            show_progress=False,
        )
        total += len(batch)

    log.info("indexed", nodes=total)
    return total


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Embed and index documents into Qdrant")
    parser.add_argument("--input", default=str(settings.processed_dir / "documents.jsonl"))
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    index_documents(Path(args.input), batch_size=args.batch_size)


if __name__ == "__main__":
    main()
