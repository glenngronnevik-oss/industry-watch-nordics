#!/usr/bin/env bash
# Ask the portal to pull from the GitHub data repo right away.
# Usage: IW_INGEST_TOKEN=... scripts/sync-now.sh [https://host]
set -euo pipefail
base="${1:-https://industrywatchnordics.gronnevik.org}"
: "${IW_INGEST_TOKEN:?set IW_INGEST_TOKEN}"
curl -fsS -X POST "${base}/api/sync" -H "Authorization: Bearer ${IW_INGEST_TOKEN}"
echo
