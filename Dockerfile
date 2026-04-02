FROM python:3.13-slim

# System deps for Playwright/Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 libxshmfence1 libgtk-3-0 \
    libx11-xcb1 fonts-liberation wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

# Install all dependencies
RUN pip install --no-cache-dir \
    "playwright>=1.40" "selectolax>=0.3" "httpx>=0.26" \
    "sqlalchemy>=2.0" "alembic>=1.13" "fastapi>=0.109" \
    "uvicorn>=0.27" "jinja2>=3.1" "apscheduler>=3.10" \
    "pydantic>=2.5" "python-dotenv>=1.0" "loguru>=0.7" \
    "anthropic>=0.40" "openai>=1.0" \
    && python -m playwright install chromium

COPY . .

RUN mkdir -p data/images

EXPOSE 10000

# Run diagnostic app that tries to import the real app and serves errors via /debug
CMD uvicorn minimal_app:app --host 0.0.0.0 --port ${PORT:-10000}
