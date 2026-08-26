#!/usr/bin/env bash
# Serve the register on the plant LAN.
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 "$@"
