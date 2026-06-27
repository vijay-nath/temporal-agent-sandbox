#!/usr/bin/env bash
# Submit a run and stream its status. Usage: scripts/demo.sh "your task [directive]"
# Directives to exercise scenarios: [fail] [hang] [oom] [net] [fork] [bigout]
set -euo pipefail

# Load .env so the token matches the running API.
set -a
[ -f .env ] && . ./.env
set +a

API="${API:-http://localhost:8000}"
TOKEN="${API_BEARER_TOKEN:-dev-token-change-me}"
TASK="${1:-Summarize the architecture of this service.}"

echo ">> POST /runs"
RUN_ID=$(
  curl -s -X POST "$API/runs" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"task\": \"${TASK}\", \"tenant_id\": \"demo\"}" \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["run_id"])'
)
echo "   run_id=${RUN_ID}"

echo ">> GET /runs/${RUN_ID}/stream (Ctrl-C to stop)"
curl -N -H "Authorization: Bearer $TOKEN" "$API/runs/${RUN_ID}/stream"
