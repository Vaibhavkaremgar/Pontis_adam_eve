#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

export LOCAL_DEV_MODE="${LOCAL_DEV_MODE:-true}"
export SERPAPI_DEBUG="${SERPAPI_DEBUG:-true}"
export MOCK_XRAY_MODE="${MOCK_XRAY_MODE:-true}"
export SERPAPI_DEBUG_LOG_DIR="${SERPAPI_DEBUG_LOG_DIR:-$BACKEND_DIR/debug_logs/serpapi}"

export PYTHONPATH="$BACKEND_DIR${PYTHONPATH:+:$PYTHONPATH}"

python - <<'PY'
import os

from app.core.config import APP_ENV, SERPAPI_API_KEY, APOLLO_API_KEY, LOCAL_DEV_MODE, MOCK_XRAY_MODE, validate_runtime_config
from app.services.redis_service import get_redis
from app.services.qdrant_service import qdrant_health_snapshot
from app.services.embedding_service import embedding_health_snapshot
from app.services.serpapi_sourcing_service import serpapi_health_snapshot
from app.services.apollo_enrichment_service import apollo_health_snapshot

validation = validate_runtime_config(production_mode=False)
critical = [item for item in validation["issues"] if item["severity"] == "critical"]
if critical:
    print("Runtime config validation failed:")
    for issue in critical:
        print(f"- {issue['key']}: {issue['message']}")
    raise SystemExit(1)

print(f"Environment: {APP_ENV}")
print(f"LOCAL_DEV_MODE={LOCAL_DEV_MODE} MOCK_XRAY_MODE={MOCK_XRAY_MODE}")
print(f"SerpAPI: {serpapi_health_snapshot()}")
print(f"Apollo: {apollo_health_snapshot()}")
print(f"Embedding: {embedding_health_snapshot()}")
print(f"Qdrant: {qdrant_health_snapshot()}")
print(f"Redis: {'ok' if get_redis() is not None else 'degraded'}")

if not MOCK_XRAY_MODE and not SERPAPI_API_KEY.strip():
    raise SystemExit("SERPAPI_API_KEY is required unless MOCK_XRAY_MODE=true")
if not APOLLO_API_KEY.strip():
    print("Apollo API key is missing; Apollo-dependent paths will stay disabled locally.")
PY

pushd "$BACKEND_DIR" >/dev/null
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload > "$ROOT_DIR/backend-uvicorn.out.log" 2> "$ROOT_DIR/backend-uvicorn.err.log" &
BACKEND_PID=$!
popd >/dev/null

pushd "$FRONTEND_DIR" >/dev/null
npm run dev -- --hostname 127.0.0.1 --port 3000 > "$ROOT_DIR/frontend-dev.out.log" 2> "$ROOT_DIR/frontend-dev.err.log" &
FRONTEND_PID=$!
popd >/dev/null

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Backend:  http://127.0.0.1:8000"
echo "Frontend: http://127.0.0.1:3000"
echo "Logs:     $ROOT_DIR/backend-uvicorn.out.log, $ROOT_DIR/backend-uvicorn.err.log, $ROOT_DIR/frontend-dev.out.log, $ROOT_DIR/frontend-dev.err.log"
wait
