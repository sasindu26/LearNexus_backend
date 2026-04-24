FROM python:3.12-slim

WORKDIR /app

# System deps for cryptography + sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install CPU-only PyTorch first (saves ~2GB vs the default CUDA build)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install remaining requirements — skip torch lines (already installed above)
RUN grep -v "^torch" requirements.txt | pip install --no-cache-dir -r /dev/stdin

# Pre-download the sentence-transformer model so first request isn't slow
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Copy application code, seed data, and scripts
COPY app/ app/
COPY scripts/ scripts/
COPY data/ data/
COPY entrypoint.sh entrypoint.sh
RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
