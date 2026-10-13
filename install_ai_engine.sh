#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || { echo "Instala ffmpeg: brew install ffmpeg"; exit 1; }
command -v uv >/dev/null || { echo "Instala uv antes de continuar."; exit 1; }
export UV_CACHE_DIR="$PWD/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PWD/.cache/python"
export UV_PYTHON_PREFERENCE=only-managed
export XDG_CACHE_HOME="$PWD/.cache"
python3 backend/install_engine.py
uv python install 3.10
uv venv --python 3.10 --allow-existing backend/.venv-vc
uv pip install --python backend/.venv-vc/bin/python -r backend/requirements-vc.txt -r backend/requirements-rvc.txt --override backend/constraints-vc.txt
backend/.venv-vc/bin/python backend/setup_models.py
echo "Motor preparado. Ejecuta ./start.sh para capturar y revisar tus nuevas muestras."
