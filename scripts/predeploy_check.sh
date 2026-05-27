#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

export ROOT_DIR
export PYTHONPATH="$BACKEND_DIR${PYTHONPATH:+:$PYTHONPATH}"

echo "Running backend bytecode validation..."
python -m compileall "$BACKEND_DIR/app" "$BACKEND_DIR/scripts"

echo "Running frontend typecheck..."
pushd "$FRONTEND_DIR" >/dev/null
npm run typecheck
popd >/dev/null

echo "Running Alembic integrity check..."
python "$BACKEND_DIR/scripts/check_alembic_integrity.py"

echo "Running runtime and schema validation..."
python - <<'PY'
import os
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

from app.core.config import DATABASE_URL, LOCAL_DEV_MODE, MOCK_XRAY_MODE, QDRANT_URL, SERPAPI_API_KEY, APOLLO_API_KEY, validate_runtime_config
from app.services.apollo_enrichment_service import apollo_health_snapshot
from app.services.embedding_service import embedding_health_snapshot
from app.services.qdrant_service import qdrant_health_snapshot
from app.services.redis_service import get_redis
from app.services.serpapi_sourcing_service import serpapi_health_snapshot

validation = validate_runtime_config(production_mode=False)
critical = [item for item in validation["issues"] if item["severity"] == "critical"]
if critical:
    print("Critical config issues detected:")
    for issue in critical:
        print(f"- {issue['key']}: {issue['message']}")
    raise SystemExit(1)

backend_root = Path(os.environ["ROOT_DIR"]) / "backend"
versions_dir = backend_root / "alembic" / "versions"
revision_pattern = re.compile(r"^\s*revision\s*=\s*['\"](?P<revision>[^'\"]+)['\"]\s*$", re.MULTILINE)
revisions: set[str] = set()
for file_path in sorted(versions_dir.glob("*.py")):
    content = file_path.read_text(encoding="utf-8")
    match = revision_pattern.search(content)
    if not match:
        raise SystemExit(f"Missing revision declaration in {file_path}")
    revision = match.group("revision")
    if revision in revisions:
        raise SystemExit(f"Duplicate Alembic revision detected: {revision}")
    revisions.add(revision)

engine = create_engine(DATABASE_URL)
inspector = inspect(engine)
required_tables = {
    "notification_workflow_tokens": {
        "id",
        "source_app",
        "job_id",
        "candidate_id",
        "token_type",
        "workflow_name",
        "token",
        "is_active",
        "status",
        "payload",
        "created_at",
        "updated_at",
    },
    "candidate_selection_sessions": {
        "id",
        "job_id",
        "status",
        "candidate_pool_snapshot",
        "batch_plan",
        "selected_candidate_ids",
        "rejected_candidate_ids",
        "selection_analysis",
        "created_at",
        "updated_at",
    },
}

for table_name, expected_columns in required_tables.items():
    if not inspector.has_table(table_name):
        raise SystemExit(f"Missing required table: {table_name}")
    actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
    missing = sorted(expected_columns - actual_columns)
    if missing:
        raise SystemExit(f"Missing columns on {table_name}: {', '.join(missing)}")

redis_client = get_redis()
if redis_client is None:
    raise SystemExit("Redis connectivity check failed")

qdrant_status = qdrant_health_snapshot()
if qdrant_status.get("status") != "ok":
    raise SystemExit(f"Qdrant health check failed: {qdrant_status}")

embedding_status = embedding_health_snapshot()
if embedding_status.get("status") != "ok":
    raise SystemExit(f"Embedding health check failed: {embedding_status}")

serpapi_status = serpapi_health_snapshot()
if serpapi_status.get("status") != "ok":
    raise SystemExit(f"SerpAPI health check failed: {serpapi_status}")

apollo_status = apollo_health_snapshot()
if apollo_status.get("status") != "ok" and not APOLLO_API_KEY.strip():
    print("Apollo API key is missing; Apollo health is intentionally degraded for local use.")

print("Runtime validation passed.")
print(f"LOCAL_DEV_MODE={LOCAL_DEV_MODE} MOCK_XRAY_MODE={MOCK_XRAY_MODE}")
print(f"SerpAPI: {serpapi_status}")
print(f"Apollo: {apollo_status}")
print(f"Qdrant: {qdrant_status}")
print(f"Embedding: {embedding_status}")
PY

echo "Predeploy checks passed."
