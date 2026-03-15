# Licensed under the Apache License, Version 2.0
# Multi-stage, multi-arch Dockerfile for FSCrawler Python edition.
# Supports linux/amd64 and linux/arm64.

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Install uv (no pip, per AGENTS.md supply-chain policy)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

# Copy lockfile + project metadata first for layer caching
COPY pyproject.toml uv.lock ./
COPY src/ src/

# Export locked deps to a plain requirements file (no bash-isms needed),
# then install to an isolated prefix so the runtime stage copies cleanly.
RUN uv export --frozen --no-dev -o /tmp/requirements.txt \
    && uv pip install --prefix=/install --no-cache -r /tmp/requirements.txt \
    && uv pip install --prefix=/install --no-cache --no-deps .

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user — matches Java image uid/gid convention
RUN groupadd -g 10001 fscrawler && \
    useradd -u 10001 -g fscrawler -m -s /sbin/nologin fscrawler

# Copy only the installed package tree — does not touch the runtime Python stdlib
COPY --from=builder /install /usr/local

# Create mount-point directories with correct ownership
RUN mkdir -p /home/fscrawler/.fscrawler /data && \
    chown -R fscrawler:fscrawler /home/fscrawler /data

USER fscrawler

# Match Java image working directory
WORKDIR /home/fscrawler

# Config dir is mounted at /home/fscrawler/.fscrawler
# Data dir is mounted at /data
VOLUME ["/home/fscrawler/.fscrawler", "/data"]

# Honour the same env var the Java image convention uses for config location
ENV FSCRAWLER_CONFIG_DIR=/home/fscrawler/.fscrawler

# No CMD — running with no arguments uses the default job name "fscrawler",
# matching dadoonet/fscrawler behaviour.
ENTRYPOINT ["fscrawler"]
