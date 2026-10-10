#!/usr/bin/env bash
# Copy the portal's data volume (current.json + history) to a tarball on the Pi.
set -euo pipefail
out="${1:-$HOME/iw-backup-$(date +%Y%m%d-%H%M%S).tar.gz}"
docker run --rm -v industry-watch_iw-data:/data:ro -v "$(dirname "$out")":/out alpine \
  tar czf "/out/$(basename "$out")" -C /data .
echo "wrote $out"
