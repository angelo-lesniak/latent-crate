# syntax=docker/dockerfile:1

# Each base image tag and its manifest digest are required build arguments
# (pins.env); the digest is what the build pulls.
ARG TOOL_PYTHON_TAG
ARG TOOL_PYTHON_DIGEST
ARG FRONTEND_NODE_TAG
ARG FRONTEND_NODE_DIGEST
ARG FRONTEND_PNPM_VERSION


# hadolint ignore=DL3006
FROM docker.io/library/python:${TOOL_PYTHON_TAG}@sha256:${TOOL_PYTHON_DIGEST} AS node-deps-tool

LABEL org.opencontainers.image.title="LatentCrate custom-node snapshot tool" \
      org.opencontainers.image.description="Creates reviewed third-party node dependency snapshots without host Python"

RUN python -m pip install --no-cache-dir packaging==25.0

COPY scripts/snapshot-node-deps.py /usr/local/bin/latentcrate-snapshot-node-deps.py

ENTRYPOINT ["python", "/usr/local/bin/latentcrate-snapshot-node-deps.py"]


# hadolint ignore=DL3006
FROM docker.io/library/python:${TOOL_PYTHON_TAG}@sha256:${TOOL_PYTHON_DIGEST} AS node-set-tool

LABEL org.opencontainers.image.title="LatentCrate custom-node set tool" \
      org.opencontainers.image.description="Installs reviewed custom-node repositories at exact commits"

# Distribution packages track the pinned base image; per-package apt pins
# would drift from its snapshot and are not maintained here.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/manage-node-set.py /usr/local/bin/latentcrate-manage-node-set.py

ENTRYPOINT ["python", "/usr/local/bin/latentcrate-manage-node-set.py"]


# Download pinned Hugging Face model files without requiring host Python.
# The hf_xet extra keeps large-file transfers resumable and efficient.
# hadolint ignore=DL3006
FROM docker.io/library/python:${TOOL_PYTHON_TAG}@sha256:${TOOL_PYTHON_DIGEST} AS model-set-tool

LABEL org.opencontainers.image.title="LatentCrate model-set tool" \
      org.opencontainers.image.description="Downloads and verifies pinned Hugging Face model files"

RUN python -m pip install --no-cache-dir "huggingface_hub[hf_xet]==1.26.0"

COPY scripts/manage-model-sets.py /usr/local/bin/latentcrate-manage-model-sets.py

ENV HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
    HF_HUB_DOWNLOAD_TIMEOUT=60 \
    HF_HUB_ETAG_TIMEOUT=30 \
    HF_HOME=/cache \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["python", "/usr/local/bin/latentcrate-manage-model-sets.py"]


# hadolint ignore=DL3006
FROM docker.io/library/node:${FRONTEND_NODE_TAG}@sha256:${FRONTEND_NODE_DIGEST} AS frontend-local-tool

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG FRONTEND_PNPM_VERSION

LABEL org.opencontainers.image.title="LatentCrate local frontend builder" \
      org.opencontainers.image.description="Builds a read-only-mounted frontend checkout with a pinned Node and pnpm toolchain" \
      io.latentcrate.frontend.pnpm-version="${FRONTEND_PNPM_VERSION}"

# Distribution packages track the pinned base image; per-package apt pins
# would drift from its snapshot and are not maintained here.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global "pnpm@${FRONTEND_PNPM_VERSION}" \
    && mkdir -p /work \
    && chmod 1777 /work

COPY services/tools/build-local-frontend.sh /usr/local/bin/latentcrate-build-local-frontend

RUN chmod 0755 /usr/local/bin/latentcrate-build-local-frontend

ENV CI=true \
    HOME=/work/home \
    XDG_CACHE_HOME=/work/cache

ENTRYPOINT ["/usr/local/bin/latentcrate-build-local-frontend"]
