# LEGi-AI Deployment Guide

Target: single VM (4 vCPU / 16 GB RAM) on 네이버클라우드 or KT클라우드.

## Prerequisites

- Docker 24+, Docker Compose plugin
- Domain pointed at the VM (for HTTPS via Caddy)
- API keys: Anthropic, 국회 Open API, law.go.kr, Voyage (optional Cohere)

## First deploy

```bash
cp .env.prod.example .env.prod
vim .env.prod            # fill keys and LEGI_DOMAIN
./scripts/deploy.sh
```

Then seed the vector index inside the container:

```bash
docker compose -f docker-compose.prod.yml exec legi-ai-api \
    python -m legi_ai.ingestion.assembly_api --target bills --since 2024-05-30
docker compose -f docker-compose.prod.yml exec legi-ai-api \
    python -m legi_ai.ingestion.assembly_api --target members
docker compose -f docker-compose.prod.yml exec legi-ai-api \
    python -m legi_ai.ingestion.law_api
docker compose -f docker-compose.prod.yml exec legi-ai-api \
    python -m legi_ai.ingestion.normalizer
docker compose -f docker-compose.prod.yml exec legi-ai-api \
    python scripts/init_qdrant.py
docker compose -f docker-compose.prod.yml exec legi-ai-api \
    python -m legi_ai.rag.indexer
```

## Daily ops

- `./scripts/backup_qdrant.sh` — schedule via cron (`0 3 * * *`)
- `docker compose -f docker-compose.prod.yml logs -f legi-ai-api` — tail logs
- `docker compose -f docker-compose.prod.yml exec legi-ai-api python -m legi_ai.rag.query_logger --days 7` — weekly cost report

## Rollback

```bash
docker compose -f docker-compose.prod.yml down
git checkout <previous-tag>
./scripts/deploy.sh
```

Qdrant data persists in `./qdrant_storage`; to restore from a snapshot:

```bash
curl -X PUT "http://localhost:6333/collections/legi_documents/snapshots/upload" \
     -F "snapshot=@backups/qdrant_legi_documents_YYYYMMDD.snapshot"
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| 502 from Caddy | API container down | `docker compose ... ps`; inspect logs |
| `VoyageAPIError` | missing/expired VOYAGE_API_KEY | update `.env.prod`; `docker compose up -d legi-ai-api` |
| `qdrant unreachable` | qdrant volume permissions | `chown -R 1000:1000 qdrant_storage` |
| Slow responses | cold cache | warm with `query_engine --samples` once |

## Monitoring

Structured JSON logs go to stdout (consumed by `docker logs` or shipped to
Loki). Optional Prometheus exporter can be added later by wiring
`prometheus-fastapi-instrumentator` into `legi_ai.api.main`.
