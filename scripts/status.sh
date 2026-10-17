#!/usr/bin/env bash
# Quick health view: LiteLLM models, GPU, recent requests, voice bank hash.
set -uo pipefail
: "${LITELLM_MASTER_KEY:?set LITELLM_MASTER_KEY}"
LOG_DIR="${LOG_DIR:-/data/logs}"
BASE="${LITELLM_URL:-http://127.0.0.1:4000}"

echo "== /health"
curl -fsS -m 30 -H "Authorization: Bearer $LITELLM_MASTER_KEY" "$BASE/health" || echo "unreachable"
echo; echo "== gpu.log (last 5)"
tail -n 5 "$LOG_DIR/gpu.log" 2>/dev/null || echo "no gpu.log"
echo "== requests.log (last 20)"
tail -n 20 "$LOG_DIR/requests.log" 2>/dev/null || echo "no requests.log"
echo "== /voices-manifest"
curl -fsS -m 5 "${BACKEND_URL:-http://127.0.0.1:8001}/voices-manifest" || echo "unreachable"
echo
