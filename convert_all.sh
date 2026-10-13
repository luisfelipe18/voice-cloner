#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/backend"
export PYTORCH_ENABLE_MPS_FALLBACK=1
exec .venv-vc/bin/python batch_convert.py "$@"
