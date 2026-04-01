FROM python:3.13-slim

# Install system deps needed by Playwright/Chromium
RUN apt-get update && apt-get install -y \
    curl wget gnupg \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
RUN pip install uv

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy dependency files first (for Docker layer caching)
COPY pyproject.toml .
COPY . .

# Install Python dependencies
RUN uv pip install --system .

# Install Playwright's Chromium browser
RUN python -m playwright install chromium

# Create data directory (Railway Volume will overlay this)
RUN mkdir -p data/images

CMD ["python", "start.py"]
