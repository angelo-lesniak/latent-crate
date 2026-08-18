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
