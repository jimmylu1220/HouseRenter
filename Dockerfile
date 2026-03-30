# 將 slim 改為 slim-bookworm，鎖定在穩定的 Debian 12 系統
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

COPY requirements.txt .

RUN apt-get update && \
    pip install --no-cache-dir -r requirements.txt && \
    playwright install-deps chromium && \
    playwright install chromium && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY . .

CMD ["python", "main.py"]