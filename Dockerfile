# Multi-stage build for efficient container size
FROM python:3.12-slim AS builder

# Build arguments
ARG VERSION="unknown"
ARG COMMIT_SHA="unknown"
ARG BUILD_DATE="unknown"

# Copy uv binary from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Compile bytecode and use copy link mode (avoids cross-device hardlink issues)
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install dependencies first (layer-cached when only src changes)
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project

# Install the project itself
COPY . .
RUN uv sync --frozen --no-dev

# Production stage — slim image, no uv, no build tools
FROM python:3.12-slim AS production

# Create non-root user for security
RUN groupadd -g 1001 linear && \
    useradd -u 1001 -g linear -s /bin/sh -m linear

# Install curl — the deployment platform overrides the container health check with
# `wget ... || curl ... || exit 1`, and python:3.12-slim ships neither wget nor curl,
# so without this the container is reported permanently unhealthy.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy virtual environment and source from builder
COPY --from=builder --chown=linear:linear /app/.venv /app/.venv
COPY --from=builder --chown=linear:linear /app/src /app/src

# Put venv on PATH so `python -m linear_mcp` resolves correctly
ENV PATH="/app/.venv/bin:$PATH"

# Default environment — HTTP transport, gateway mode
ENV MCP_TRANSPORT=http
ENV MCP_HTTP_PORT=8080
ENV MCP_HTTP_HOST=0.0.0.0

USER linear

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["python", "-m", "linear_mcp"]

# OCI image labels
LABEL org.opencontainers.image.title="linear-mcp"
LABEL org.opencontainers.image.description="Linear MCP server — Model Context Protocol server for Linear"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"
LABEL org.opencontainers.image.revision="${COMMIT_SHA}"
LABEL org.opencontainers.image.licenses="Apache-2.0"
