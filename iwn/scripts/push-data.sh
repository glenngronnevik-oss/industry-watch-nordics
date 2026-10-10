#!/usr/bin/env bash
# Manually push a data file to the portal's ingest API.
# Usage: IW_INGEST_TOKEN=... scripts/push-data.sh path/to/industry-watch-data.js [https://host]
# Add FORCE=1 to accept data older than what the portal has (rollback).
set -euo pipefail
file="${1:?usage: push-data.sh FILE [BASE_URL]}"
base="${2:-https://industrywatchnordics.gronnevik.org}"
: "${IW_INGEST_TOKEN:?set IW_INGEST_TOKEN}"
q=""; [[ "${FORCE:-0}" == "1" ]] && q="?force=1"
curl -fsS -X POST "${base}/api/ingest${q}" \
  -H "Authorization: Bearer ${IW_INGEST_TOKEN}" \
  -H "Content-Type: text/javascript" \
  --data-binary "@${file}"
echo
