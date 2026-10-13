#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mode="${1:-fine_tune}"
[[ "$mode" == "initial" || "$mode" == "fine_tune" ]] || { echo "Usa initial o fine_tune."; exit 1; }
command -v ffmpeg >/dev/null || { echo "Instala ffmpeg y libsndfile1 con apt en la celda de Colab."; exit 1; }
export UV_CACHE_DIR="$PWD/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PWD/.cache/python"
export UV_PYTHON_PREFERENCE=only-managed
export XDG_CACHE_HOME="$PWD/.cache"
export PIP_CACHE_DIR="$PWD/.cache/pip"
python3 -m pip install --target "$PWD/.tools" 'uv>=0.8,<1'
export PYTHONPATH="$PWD/.tools${PYTHONPATH:+:$PYTHONPATH}"
python3 backend/install_engine.py
python3 -m uv python install 3.10
python3 -m uv venv --python 3.10 --allow-existing backend/.venv-vc
python3 -m uv pip install --python backend/.venv-vc/bin/python torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
python3 -m uv pip install --python backend/.venv-vc/bin/python -r backend/requirements-vc.txt -r backend/requirements-rvc.txt --override backend/constraints-vc.txt
backend/.venv-vc/bin/python -c 'import torch; assert torch.cuda.is_available(), "CUDA no disponible: selecciona runtime GPU y comprueba el driver"; x=torch.ones(4,device="cuda"); assert (x+x).sum().item()==8; print(torch.cuda.get_device_name(),torch.version.cuda)'
args=(--training-only)
[[ "$mode" != "fine_tune" ]] || args+=(--skip-pretrained)
backend/.venv-vc/bin/python backend/setup_models.py "${args[@]}"
echo "Colab GPU listo. Importa tu paquete privado con backend/portable.py."
