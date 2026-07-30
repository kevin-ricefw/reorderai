# Python
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# LightGBM needs libgomp
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Writable dirs for uploads / training outputs inside the container
RUN mkdir -p data/sales data/inventory data/vendors data/cache data/waste \
    outputs/analytics outputs/jobs models

EXPOSE 8080

# Cloud Run sets PORT; training can exceed default request timeouts — train is async.
CMD exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --workers 1
