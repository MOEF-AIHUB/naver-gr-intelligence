"""Initialize the Qdrant collection for LEGi-AI."""
from __future__ import annotations

import sys

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from legi_ai.config import settings
from legi_ai.logging import configure_logging, get_logger

log = get_logger(__name__)


def ensure_collection() -> None:
    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    name = settings.qdrant_collection
    dims = settings.embedding_dim

    existing = {c.name for c in client.get_collections().collections}
    if name in existing:
        log.info("collection exists", name=name)
    else:
        client.create_collection(
            collection_name=name,
            vectors_config=qm.VectorParams(size=dims, distance=qm.Distance.COSINE),
            optimizers_config=qm.OptimizersConfigDiff(indexing_threshold=20000),
        )
        log.info("created collection", name=name, dims=dims)

    for field, schema in [
        ("source_type", qm.PayloadSchemaType.KEYWORD),
        ("created_at", qm.PayloadSchemaType.DATETIME),
        ("valid_from", qm.PayloadSchemaType.KEYWORD),
        ("law_id", qm.PayloadSchemaType.KEYWORD),
        ("member_id", qm.PayloadSchemaType.KEYWORD),
        ("bill_id", qm.PayloadSchemaType.KEYWORD),
        ("committee", qm.PayloadSchemaType.KEYWORD),
        ("party", qm.PayloadSchemaType.KEYWORD),
    ]:
        try:
            client.create_payload_index(
                collection_name=name,
                field_name=field,
                field_schema=schema,
            )
            log.info("payload index created", field=field)
        except Exception as exc:
            log.debug("payload index exists or failed", field=field, error=str(exc))


def healthcheck() -> bool:
    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    try:
        info = client.get_collection(settings.qdrant_collection)
        log.info(
            "healthy",
            collection=settings.qdrant_collection,
            vectors=info.points_count,
        )
        return True
    except Exception as exc:
        log.error("qdrant unreachable", error=str(exc))
        return False


def main() -> None:
    configure_logging()
    ensure_collection()
    ok = healthcheck()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
