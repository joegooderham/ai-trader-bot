# ─────────────────────────────────────────────────────────────────────────────
# AI Trader Dashboard — Multi-stage build
# Stage 1: Build React frontend with Node
# Stage 2: Run FastAPI backend with Python, serving built static files
# ─────────────────────────────────────────────────────────────────────────────

# Stage 1: Build React frontend
FROM node:20-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ .
RUN npm run build

# Stage 2: Python backend + built frontend
FROM python:3.11-slim

# Install git for wiki cloning
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ .

# Copy built React frontend from stage 1
COPY --from=frontend-build /build/dist /app/frontend/dist

# Dashboard runs on port 8050
EXPOSE 8050

# Run with uvicorn — single worker is fine for a personal dashboard
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8050"]
