#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT_DIR"
set -a
source .env
set +a
exec python backend/manage.py runserver 127.0.0.1:8000
