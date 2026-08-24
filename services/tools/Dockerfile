# syntax=docker/dockerfile:1

ARG TOOL_PYTHON_IMAGE
ARG FRONTEND_NODE_IMAGE


# The base image is pinned through a required build argument (versions/*.env).
# hadolint ignore=DL3006
FROM ${TOOL_PYTHON_IMAGE} AS node-deps-tool

LABEL org.opencontainers.image.title="LatentCrate custom-node snapshot tool" \
      org.opencontainers.image.description="Creates reviewed third-party node dependency snapshots without host Python"

RUN python -m pip install --no-cache-dir packaging==25.0

COPY scripts/snapshot-node-deps.py /usr/local/bin/latentcrate-snapshot-node-deps.py

ENTRYPOINT ["python", "/usr/local/bin/latentcrate-snapshot-node-deps.py"]


# Shared CA-enabled base for read-only maintenance tools that query public
# version sources without requiring host Python or repository mounts.
# The base image is pinned through a required build argument (versions/*.env).
# hadolint ignore=DL3006
FROM ${TOOL_PYTHON_IMAGE} AS network-python-tool

# Distribution packages track the pinned base image; per-package apt pins
# would drift from its snapshot and are not maintained here.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*


# Download and validate a release archive without requiring host Python or a
# writable repository mount.
FROM network-python-tool AS frontend-release-tool

LABEL org.opencontainers.image.title="LatentCrate frontend release tool" \
      org.opencontainers.image.description="Calculates the digest of an exact ComfyUI frontend release archive"

COPY services/comfy/install-release-frontend.py /usr/local/bin/latentcrate-frontend-release.py

ENTRYPOINT ["python", "/usr/local/bin/latentcrate-frontend-release.py"]


# Resolve stable upstream versions. The wrapper validates the labeled output
# and remains solely responsible for updating the host profile atomically.
FROM network-python-tool AS version-update-tool

LABEL org.opencontainers.image.title="LatentCrate version update tool" \
      org.opencontainers.image.description="Resolves eligible releases and image or package versions from public sources"

COPY services/comfy/install-release-frontend.py /usr/local/bin/latentcrate-frontend-release.py
COPY services/tools/update-versions.py /usr/local/bin/latentcrate-update-versions.py

ENTRYPOINT ["python", "/usr/local/bin/latentcrate-update-versions.py"]


# The base image is pinned through a required build argument (versions/*.env).
# hadolint ignore=DL3006
FROM ${TOOL_PYTHON_IMAGE} AS node-set-tool

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
# The base image is pinned through a required build argument (versions/*.env).
# hadolint ignore=DL3006
FROM ${TOOL_PYTHON_IMAGE} AS model-set-tool

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


# The base image is pinned through a required build argument (versions/*.env).
# hadolint ignore=DL3006
FROM ${FRONTEND_NODE_IMAGE} AS frontend-local-tool

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG FRONTEND_NODE_IMAGE
ARG FRONTEND_PNPM_VERSION

LABEL org.opencontainers.image.title="LatentCrate local frontend builder" \
      org.opencontainers.image.description="Builds a read-only-mounted frontend checkout with a pinned Node and pnpm toolchain" \
      io.latentcrate.frontend.node-image="${FRONTEND_NODE_IMAGE}" \
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
