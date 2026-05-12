# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir poetry==2.1.4
WORKDIR /app
COPY pyproject.toml poetry.lock* ./
RUN poetry install --only main --no-interaction --no-ansi

FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        wget gnupg ca-certificates curl unzip \
        fonts-liberation libnss3 libxss1 libasound2 libatk-bridge2.0-0 \
        libgtk-3-0 libgbm1 libxkbcommon0 libdrm2 libxcomposite1 libxdamage1 \
        libxrandr2 libxshmfence1 libxfixes3 libpango-1.0-0 libcairo2 \
    && wget -q -O /usr/share/keyrings/googlechrome-linux-keyring.gpg \
        https://dl-ssl.google.com/linux/linux_signing_key.pub \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/googlechrome-linux-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .

ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
