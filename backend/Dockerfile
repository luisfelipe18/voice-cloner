FROM python:3.10-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git libsndfile1 && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace/backend
COPY requirements-vc.txt requirements-rvc.txt constraints-vc.txt ./
RUN pip install uv && uv pip install --system torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cpu && uv pip install --system -r requirements-vc.txt -r requirements-rvc.txt --override constraints-vc.txt
COPY install_engine.py ./
COPY vendor ./vendor
RUN python install_engine.py
COPY app ./app
COPY setup_models.py rvc_infer.py portable.py ./
ENV PYTORCH_ENABLE_MPS_FALLBACK=1
CMD ["sh", "-c", "python setup_models.py && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
