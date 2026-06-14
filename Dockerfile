# ============================================
# MHTI - Multi-stage Docker Build
# Caddy (reverse proxy) + FastAPI (API)
# ============================================

# Stage 1: Build frontend
FROM node:24-alpine AS frontend-builder

WORKDIR /app

COPY web/package*.json ./
RUN npm ci --silent

COPY web/ .
RUN npm run build

# Stage 2: Runtime environment
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="MHTI"
LABEL org.opencontainers.image.description="Media metadata scraper with TMDB integration"
LABEL org.opencontainers.image.version="1.0.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/app/data

WORKDIR /app

# Install system dependencies + Caddy + build tools (for p115cipher/orjson C extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    debian-keyring \
    debian-archive-keyring \
    apt-transport-https \
    gcc \
    g++ \
    libffi-dev \
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list \
    && apt-get update \
    && apt-get install -y caddy \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files and install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove gcc g++ libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy backend source code
COPY server/ ./server/

# Copy frontend build artifacts
COPY --from=frontend-builder /app/dist /app/static/

# Copy Caddy configuration
COPY Caddyfile /etc/caddy/Caddyfile

# Copy startup script
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Create data directory
RUN mkdir -p /app/data && chmod 755 /app/data

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Expose ports
EXPOSE 8000

CMD ["/app/start.sh"]
