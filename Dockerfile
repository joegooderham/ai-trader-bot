# ─────────────────────────────────────────────────────────────────────────────
# Joseph's Forex Bot - Dockerfile
#
# This builds a container that runs identically on Windows, Mac, or Linux.
# Based on Python 3.11 (slim = smaller image, faster to download/start)
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# Set working directory inside the container
WORKDIR /app

# Install system dependencies needed for some Python libraries
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (Docker caches this layer — speeds up rebuilds)
COPY requirements.txt .

# Install all Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project into the container
COPY . .

# Create directories for data persistence
RUN mkdir -p /app/data /app/logs

# Default command — runs the main trading bot scheduler
CMD ["python", "-m", "bot.scheduler"]
