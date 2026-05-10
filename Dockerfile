# Hydra Final V16.5 — Railway-ready Dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# System deps + Google Chrome (for nodriver/playwright stealth)
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

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway injects $PORT at runtime; default to 8080 for local
ENV PORT=8080
EXPOSE 8080

# Use shell form so $PORT expands at runtime
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
