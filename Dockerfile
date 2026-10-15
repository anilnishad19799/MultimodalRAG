FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr libmagic1 poppler-utils libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/app
COPY pyproject.toml requirements.txt ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
# Install matching CPU-only PyTorch and TorchVision wheels before application
# dependencies. Transformers' optional vision integration must see a compatible
# Torch/TorchVision pair even though the RAG embedding model is text-only.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && python -c "import torch, torchvision; assert torch.version.cuda is None, 'Expected CPU-only PyTorch'; print(f'torch={torch.__version__}, torchvision={torchvision.__version__}, cuda={torch.version.cuda}')" \
    && pip install --no-cache-dir . \
    && python -c "from transformers import PreTrainedModel; from sentence_transformers import SentenceTransformer; print('Embedding dependencies import successfully')"
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
