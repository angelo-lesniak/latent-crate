# syntax=docker/dockerfile:1

# Stage map. Build-only: media-builder (FFmpeg and its codec libraries),
# torchcodec-runtime-libs (CUDA NPP libraries and the CUDA family check),
# node-deps-builder (third-party node wheels), node-package-installer (their
# offline install as an unprivileged user), frontend-git-builder (frontend web
# files from one Git commit), sage-builder (the SageAttention wheel). Shared:
# comfy-python-base (the ComfyUI Python environment), comfy-runtime-base
# (media libraries, configuration, entrypoint). Targets: comfy-runtime (no
# frontend; the dist overlay serves one), runtime (frontend from Git),
# runtime-sage (runtime plus SageAttention), default (runtime).

ARG PYTORCH_DEVEL_TAG
ARG PYTORCH_DEVEL_DIGEST
ARG PYTORCH_RUNTIME_TAG
ARG PYTORCH_RUNTIME_DIGEST
ARG FRONTEND_NODE_TAG
ARG FRONTEND_NODE_DIGEST

ARG COMFYUI_REF
ARG COMFYUI_FRONTEND_REF
ARG FFMPEG_REF
ARG NV_CODEC_HEADERS_REF
ARG SVT_AV1_REF
ARG SAGEATTENTION_REF
ARG FRONTEND_PNPM_VERSION
ARG CUDA_NPP_VERSION
ARG TORCHCODEC_VERSION
ARG COMFY_CUDA_ARCH_LIST
ARG SAGE_BUILD_JOBS
ARG FRONTEND_GIT_URL
ARG FRONTEND_GIT_REF
ARG FRONTEND_GIT_REQUESTED_REF


# -----------------------------------------------------------------------------
# Media and ComfyUI foundations
# -----------------------------------------------------------------------------

# Build-only: compiles FFmpeg and its codec libraries. Later stages copy the
# finished media files and build records without copying the compiler toolchain.
# The base image tag and its manifest digest are required build arguments
# (pins.env); the digest is what the build pulls.
# hadolint ignore=DL3006
FROM docker.io/pytorch/pytorch:${PYTORCH_DEVEL_TAG}@sha256:${PYTORCH_DEVEL_DIGEST} AS media-builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG PYTORCH_DEVEL_TAG
ARG FFMPEG_REF
ARG NV_CODEC_HEADERS_REF
ARG SVT_AV1_REF

ENV DEBIAN_FRONTEND=noninteractive \
    PKG_CONFIG_PATH=/opt/latentcrate-media/lib/pkgconfig

# Distribution packages track the pinned base image; per-package apt pins
# would drift from its snapshot and are not maintained here.
# hadolint ignore=DL3008
RUN --mount=type=cache,id=latentcrate-apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=latentcrate-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        autoconf \
        automake \
        build-essential \
        ca-certificates \
        cmake \
        git \
        libaom-dev \
        libass-dev \
        libdav1d-dev \
        libfreetype6-dev \
        libopus-dev \
        libtool \
        libvpx-dev \
        libx264-dev \
        libx265-dev \
        nasm \
        ninja-build \
        pkg-config \
        yasm

RUN git init /tmp/nv-codec-headers \
    && git -C /tmp/nv-codec-headers remote add origin https://github.com/FFmpeg/nv-codec-headers.git \
    && git -C /tmp/nv-codec-headers fetch --depth 1 origin "${NV_CODEC_HEADERS_REF}" \
    && git -C /tmp/nv-codec-headers checkout --detach FETCH_HEAD \
    && make -C /tmp/nv-codec-headers PREFIX=/opt/latentcrate-media install \
    && mkdir -p /opt/latentcrate-build-info \
    && printf '%s\n' "${PYTORCH_DEVEL_TAG}" > /opt/latentcrate-build-info/pytorch-devel.ref \
    && printf '%s\n' "${NV_CODEC_HEADERS_REF}" > /opt/latentcrate-build-info/nv-codec-headers.ref \
    && git -C /tmp/nv-codec-headers rev-parse HEAD > /opt/latentcrate-build-info/nv-codec-headers.commit \
    && rm -rf /tmp/nv-codec-headers

RUN git init /tmp/svt-av1 \
    && git -C /tmp/svt-av1 remote add origin https://gitlab.com/AOMediaCodec/SVT-AV1.git \
    && git -C /tmp/svt-av1 fetch --depth 1 origin "${SVT_AV1_REF}" \
    && git -C /tmp/svt-av1 checkout --detach FETCH_HEAD \
    && cmake -S /tmp/svt-av1 -B /tmp/svt-av1/build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/latentcrate-media \
        -DBUILD_DEC=OFF \
        -DBUILD_ENC=ON \
        -DBUILD_SHARED_LIBS=ON \
    && cmake --build /tmp/svt-av1/build --parallel "$(nproc)" \
    && cmake --install /tmp/svt-av1/build \
    && printf '%s\n' "${SVT_AV1_REF}" > /opt/latentcrate-build-info/svt-av1.ref \
    && git -C /tmp/svt-av1 rev-parse HEAD > /opt/latentcrate-build-info/svt-av1.commit \
    && rm -rf /tmp/svt-av1

# FFmpeg's configure script must run from inside its throwaway source tree.
# hadolint ignore=DL3003
RUN git init /tmp/ffmpeg \
    && git -C /tmp/ffmpeg remote add origin https://github.com/FFmpeg/FFmpeg.git \
    && git -C /tmp/ffmpeg fetch --depth 1 origin "${FFMPEG_REF}" \
    && git -C /tmp/ffmpeg checkout --detach FETCH_HEAD \
    && cd /tmp/ffmpeg \
    && ./configure \
        --prefix=/opt/latentcrate-media \
        --extra-cflags=-I/opt/latentcrate-media/include \
        --extra-ldflags=-L/opt/latentcrate-media/lib \
        --disable-debug \
        --disable-doc \
        --disable-static \
        --enable-shared \
        --enable-gpl \
        --enable-nvenc \
        --enable-libsvtav1 \
        --enable-libaom \
        --enable-libdav1d \
        --enable-libvpx \
        --enable-libx264 \
        --enable-libx265 \
        --enable-libopus \
        --enable-libass \
        --enable-libfreetype \
    && make -j"$(nproc)" \
    && make install \
    && printf '%s\n' "${FFMPEG_REF}" > /opt/latentcrate-build-info/ffmpeg.ref \
    && git -C /tmp/ffmpeg rev-parse HEAD > /opt/latentcrate-build-info/ffmpeg.commit \
    && rm -rf /tmp/ffmpeg


# Build-only: checks that the three CUDA-family pins agree and collects the
# small set of CUDA NPP libraries used by TorchCodec.
# The base image tag and its manifest digest are required build arguments
# (pins.env); the digest is what the build pulls.
# hadolint ignore=DL3006
FROM docker.io/pytorch/pytorch:${PYTORCH_DEVEL_TAG}@sha256:${PYTORCH_DEVEL_DIGEST} AS torchcodec-runtime-libs

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG CUDA_NPP_VERSION
ARG PYTORCH_DEVEL_TAG
ARG PYTORCH_RUNTIME_TAG
ARG TORCHCODEC_VERSION

# The TorchCodec wheel index is derived from the +cuNNN suffix of its version
# (comfy-python-base uses the same expression), so that suffix, the cudaNN.N of
# the PyTorch tag, and the NPP major must name one CUDA family, and the two
# PyTorch tags must name one release.
RUN family=${TORCHCODEC_VERSION##*+} \
    && cuda=${PYTORCH_RUNTIME_TAG#*-cuda} \
    && cuda=${cuda%%-*} \
    && { [ "$family" = "cu${cuda%.*}${cuda#*.}" ] && [ "${CUDA_NPP_VERSION%%.*}" = "${cuda%.*}" ] \
         && [ "${PYTORCH_DEVEL_TAG%-devel}" = "${PYTORCH_RUNTIME_TAG%-runtime}" ]; } \
      || { printf 'LatentCrate: PYTORCH_DEVEL_TAG %s, PYTORCH_RUNTIME_TAG %s, TORCHCODEC_VERSION %s, and CUDA_NPP_VERSION %s do not name one PyTorch release and CUDA family; change the four pins together.\n' \
             "${PYTORCH_DEVEL_TAG}" "${PYTORCH_RUNTIME_TAG}" "${TORCHCODEC_VERSION}" "${CUDA_NPP_VERSION}" >&2; exit 1; } \
    && mkdir -p /opt/latentcrate-npp/lib \
    && npp_soname=${CUDA_NPP_VERSION%%.*} \
    && for library in nppc nppicc; do \
         source="/usr/local/cuda/targets/x86_64-linux/lib/lib${library}.so.${CUDA_NPP_VERSION}"; \
         test -f "$source" \
         && cp "$source" /opt/latentcrate-npp/lib/ \
         && ln -s "lib${library}.so.${CUDA_NPP_VERSION}" \
             "/opt/latentcrate-npp/lib/lib${library}.so.${npp_soname}" \
         || exit 1; \
       done \
    && printf '%s\n' "${CUDA_NPP_VERSION}" > /opt/latentcrate-npp/version


# Shared foundation: installs the pinned ComfyUI Python environment and records
# the exact package state used by runtime and third-party node dependency stages.
# The base image tag and its manifest digest are required build arguments
# (pins.env); the digest is what the build pulls.
# hadolint ignore=DL3006
FROM docker.io/pytorch/pytorch:${PYTORCH_RUNTIME_TAG}@sha256:${PYTORCH_RUNTIME_DIGEST} AS comfy-python-base

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG COMFYUI_REF
ARG PYTORCH_RUNTIME_TAG
ARG TORCHCODEC_VERSION

LABEL org.opencontainers.image.title="LatentCrate ComfyUI" \
      org.opencontainers.image.description="A pinned ComfyUI runtime for Linux, NVIDIA CDI, Docker, and Podman" \
      io.latentcrate.base-image="docker.io/pytorch/pytorch:${PYTORCH_RUNTIME_TAG}"

ENV HOME=/data/home \
    HF_HOME=/cache/huggingface \
    HUGGINGFACE_HUB_CACHE=/cache/huggingface/hub \
    LD_LIBRARY_PATH=/opt/latentcrate-media/lib:/opt/latentcrate-npp/lib:${LD_LIBRARY_PATH} \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
    NVIDIA_VISIBLE_DEVICES=void \
    PATH=/opt/latentcrate-venv/bin:/opt/latentcrate-media/bin:${PATH} \
    PIP_CACHE_DIR=/cache/pip \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_PREFER_BINARY=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    PYTHONPATH=/opt/comfyui \
    PYTHONUNBUFFERED=1 \
    XDG_CACHE_HOME=/cache/xdg

# Distribution packages track the pinned base image; per-package apt pins
# would drift from its snapshot and are not maintained here.
# Deliberately omitted from this install list; add one back only when a tested
# third-party node needs it: libcairo2, libsndfile1, ocl-icd-libopencl1, and
# wget. python3-venv is build-only and is purged after creating
# /opt/latentcrate-venv in the same layer.
# hadolint ignore=DL3008
RUN --mount=type=cache,id=latentcrate-apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=latentcrate-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && DEBIAN_FRONTEND=noninteractive apt-get update \
    && apt-get install -y --no-install-recommends \
        aria2 \
        bash \
        ca-certificates \
        curl \
        fontconfig \
        fonts-dejavu-core \
        git \
        git-lfs \
        libaom3 \
        libass9 \
        libdav1d7 \
        libfreetype6 \
        libgl1 \
        libglib2.0-0 \
        libjpeg-turbo8 \
        libnuma1 \
        libopenjp2-7 \
        libopus0 \
        libpng16-16 \
        libsm6 \
        libtiff6 \
        libvpx9 \
        libwebp7 \
        libx11-6 \
        libx264-164 \
        libx265-199 \
        libxcb1 \
        libxext6 \
        libxfixes3 \
        libxrender1 \
        p7zip-full \
        python3-venv \
        unzip \
    && /usr/bin/python3 -m venv --system-site-packages /opt/latentcrate-venv \
    && DEBIAN_FRONTEND=noninteractive apt-get purge -y --auto-remove python3-venv

COPY config/python/runtime-constraints.txt /etc/latentcrate/runtime-constraints.txt
COPY config/comfy/manager-config.ini /etc/latentcrate/manager-config.ini

RUN mkdir -p /usr/local/share/latentcrate \
    && printf '%s\n' "${PYTORCH_RUNTIME_TAG}" > /usr/local/share/latentcrate/pytorch-runtime.ref \
    && /opt/latentcrate-venv/bin/python -c "from importlib.metadata import version; names = ('torch', 'torchvision', 'torchaudio', 'triton', 'numpy'); print('\\n'.join(f'{name}=={version(name)}' for name in names))" \
        > /etc/latentcrate/core-constraints.txt \
    && cat /etc/latentcrate/runtime-constraints.txt >> /etc/latentcrate/core-constraints.txt

ENV PIP_CONSTRAINT=/etc/latentcrate/core-constraints.txt

# The pip cache lives in a BuildKit cache mount, not in the image layer. The
# wheel index follows the +cuNNN suffix of the version; torchcodec-runtime-libs
# checks that suffix against the CUDA family of the base image.
# hadolint ignore=DL3042
RUN --mount=type=cache,id=latentcrate-pip-runtime,target=/cache/pip \
    python -m pip install \
        --index-url "https://download.pytorch.org/whl/${TORCHCODEC_VERSION##*+}" \
        "torchcodec==${TORCHCODEC_VERSION}" \
    && printf 'torchcodec==%s\n' "${TORCHCODEC_VERSION}" >> /etc/latentcrate/core-constraints.txt \
    && printf '%s\n' "${TORCHCODEC_VERSION}" > /usr/local/share/latentcrate/torchcodec.version

# The pip cache lives in a BuildKit cache mount, not in the image layer.
# hadolint ignore=DL3042
RUN --mount=type=cache,id=latentcrate-pip-runtime,target=/cache/pip \
    git init /opt/comfyui \
    && git -C /opt/comfyui remote add origin https://github.com/Comfy-Org/ComfyUI.git \
    && git -C /opt/comfyui fetch --depth 1 origin "${COMFYUI_REF}" \
    && git -C /opt/comfyui checkout --detach FETCH_HEAD \
    && git -C /opt/comfyui rev-parse HEAD > /usr/local/share/latentcrate/comfyui.commit \
    && printf '%s\n' "${COMFYUI_REF}" > /usr/local/share/latentcrate/comfyui.ref \
    && python -m pip install -r /opt/comfyui/requirements.txt \
    && if [ -f /opt/comfyui/manager_requirements.txt ]; then \
        python -m pip install -r /opt/comfyui/manager_requirements.txt; \
       fi \
    && python -m pip freeze --all | LC_ALL=C sort \
        > /etc/latentcrate/comfy-environment-constraints.txt

RUN sha256sum \
        /etc/latentcrate/core-constraints.txt \
        /etc/latentcrate/comfy-environment-constraints.txt \
        /usr/local/share/latentcrate/comfyui.commit \
        /usr/local/share/latentcrate/pytorch-runtime.ref \
        > /tmp/python-environment.manifest \
    && find /opt/comfyui -maxdepth 1 -type f -name '*requirements.txt' -print0 \
        | sort -z \
        | xargs -0 -r sha256sum >> /tmp/python-environment.manifest \
    && cat /etc/latentcrate/comfy-environment-constraints.txt \
        >> /tmp/python-environment.manifest \
    && sha256sum /tmp/python-environment.manifest \
        | cut -d ' ' -f 1 > /usr/local/share/latentcrate/base-python-environment.id \
    && mv /tmp/python-environment.manifest \
        /usr/local/share/latentcrate/base-python-environment.manifest


# Shared runtime: adds media libraries, configuration, startup scripts, and
# health checks to the pinned ComfyUI Python foundation.
FROM comfy-python-base AS comfy-runtime-base

ARG COMFYUI_REF
ARG CUDA_NPP_VERSION
ARG FFMPEG_REF
ARG NV_CODEC_HEADERS_REF
ARG SVT_AV1_REF
ARG TORCHCODEC_VERSION

COPY --from=media-builder /opt/latentcrate-media /opt/latentcrate-media
COPY --from=media-builder /opt/latentcrate-build-info /usr/local/share/latentcrate
COPY --from=torchcodec-runtime-libs /opt/latentcrate-npp /opt/latentcrate-npp

COPY services/comfy/entrypoint.sh /usr/local/bin/latentcrate-entrypoint
COPY services/comfy/healthcheck.py /usr/local/bin/latentcrate-healthcheck.py
COPY services/comfy/gpu-smoke.sh /usr/local/bin/latentcrate-gpu-smoke
COPY scripts/manage-templates.py /usr/local/bin/latentcrate-manage-templates.py
COPY config/comfy/extra_model_paths.yaml /etc/latentcrate/extra_model_paths.yaml
COPY config/python/custom-node-constraints.txt /etc/latentcrate/custom-node-constraints.txt

RUN chmod 0755 \
        /usr/local/bin/latentcrate-entrypoint \
        /usr/local/bin/latentcrate-gpu-smoke \
    && test "$(python -c 'from importlib.metadata import version; print(version("torchcodec"))')" = "${TORCHCODEC_VERSION}" \
    && ffmpeg -hide_banner -loglevel error -f lavfi -i color=size=64x64:rate=1 -frames:v 2 -c:v libx264 -y /tmp/torchcodec-smoke.mp4 \
    && TORCHCODEC_SMOKE_FILE=/tmp/torchcodec-smoke.mp4 python -c 'import os; from torchcodec.decoders import VideoDecoder; decoder = VideoDecoder(os.environ["TORCHCODEC_SMOKE_FILE"], device="cpu"); frame = decoder[0]; assert tuple(frame.shape) == (3, 64, 64)' \
    && rm -f /tmp/torchcodec-smoke.mp4

LABEL io.latentcrate.comfyui.ref="${COMFYUI_REF}" \
      io.latentcrate.ffmpeg.ref="${FFMPEG_REF}" \
      io.latentcrate.nv-codec-headers.ref="${NV_CODEC_HEADERS_REF}" \
      io.latentcrate.svt-av1.ref="${SVT_AV1_REF}" \
      io.latentcrate.sageattention.enabled="false" \
      io.latentcrate.cuda-npp.version="${CUDA_NPP_VERSION}" \
      io.latentcrate.torchcodec.version="${TORCHCODEC_VERSION}"

WORKDIR /opt/comfyui

ENV LATENTCRATE_SAGE_ENABLED=0

EXPOSE 8188

ENTRYPOINT ["/usr/local/bin/latentcrate-entrypoint"]

# -----------------------------------------------------------------------------
# Third-party node dependency environment
# -----------------------------------------------------------------------------

# Build-only: resolves and builds saved third-party node requirements with the same
# CUDA and Python package versions used by ComfyUI.
# The base image tag and its manifest digest are required build arguments
# (pins.env); the digest is what the build pulls.
# hadolint ignore=DL3006
FROM docker.io/pytorch/pytorch:${PYTORCH_DEVEL_TAG}@sha256:${PYTORCH_DEVEL_DIGEST} AS node-deps-builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG COMFY_CUDA_ARCH_LIST
ARG PYTORCH_DEVEL_TAG

ENV CUDA_HOME=/usr/local/cuda \
    CUDACXX=/usr/local/cuda/bin/nvcc \
    DEBIAN_FRONTEND=noninteractive \
    MAX_JOBS=8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_PREFER_BINARY=1 \
    PATH=/opt/latentcrate-venv/bin:${PATH} \
    TORCH_CUDA_ARCH_LIST=${COMFY_CUDA_ARCH_LIST}

# Distribution packages track the pinned base image; per-package apt pins
# would drift from its snapshot and are not maintained here.
# hadolint ignore=DL3008
RUN --mount=type=cache,id=latentcrate-apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=latentcrate-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
        ninja-build \
        pkg-config

COPY --from=comfy-python-base /opt/latentcrate-venv /opt/latentcrate-venv
COPY --from=comfy-python-base \
    /etc/latentcrate/comfy-environment-constraints.txt \
    /etc/latentcrate/comfy-environment-constraints.txt
COPY --from=comfy-python-base \
    /etc/latentcrate/core-constraints.txt \
    /etc/latentcrate/core-constraints.txt
COPY config/python/custom-node-constraints.txt /etc/latentcrate/custom-node-constraints.txt
COPY services/comfy/build-node-deps.sh /usr/local/bin/latentcrate-build-node-deps
COPY services/comfy/create-node-package-lock.py /usr/local/lib/latentcrate/create-node-package-lock.py
COPY services/comfy/create-node-resolution.py /usr/local/lib/latentcrate/create-node-resolution.py
COPY services/comfy/rewrite-node-requirements.py /usr/local/lib/latentcrate/rewrite-node-requirements.py
COPY config/custom-nodes/vcs-pins.toml config/custom-nodes/allowed-git-hosts.txt /etc/latentcrate/
COPY config/python/custom-node-package-replacements.toml /etc/latentcrate/custom-node-package-replacements.toml

# SHELL (bash with pipefail) is set in this stage, but hadolint's ShellCheck
# pass still assumes POSIX sh and misreads [[ ]].
# hadolint ignore=SC3010
RUN chmod 0755 /usr/local/bin/latentcrate-build-node-deps \
    && python -m pip freeze --all | LC_ALL=C sort > /tmp/devel-python-environment.txt \
    && while IFS= read -r requirement || [[ -n "$requirement" ]]; do \
         [[ -z "$requirement" ]] \
           || grep -Fqx -- "$requirement" /tmp/devel-python-environment.txt \
           || { printf 'LatentCrate: CUDA build environment does not match runtime: %s\n' "$requirement" >&2; exit 1; }; \
       done < /etc/latentcrate/comfy-environment-constraints.txt \
    && rm -f /tmp/devel-python-environment.txt

# The saved requirement tree is copied rather than bind-mounted so that BuildKit
# and Buildah both key this stage on its content and the rewrite can edit it.
COPY build/custom-node-requirements /tmp/custom-node-requirements

RUN python /usr/local/lib/latentcrate/rewrite-node-requirements.py /tmp/custom-node-requirements \
        --vcs-pins /etc/latentcrate/vcs-pins.toml \
        --package-replacements /etc/latentcrate/custom-node-package-replacements.toml \
        --allowed-git-hosts /etc/latentcrate/allowed-git-hosts.txt

RUN --mount=type=cache,id=latentcrate-pip-node-${PYTORCH_DEVEL_TAG}-${COMFY_CUDA_ARCH_LIST},target=/root/.cache/pip,sharing=locked \
    CMAKE_PREFIX_PATH="$(python -c 'import torch; print(torch.utils.cmake_prefix_path)')${CMAKE_PREFIX_PATH:+:${CMAKE_PREFIX_PATH}}" \
    && export CMAKE_PREFIX_PATH \
    && /usr/local/bin/latentcrate-build-node-deps \
        /tmp/custom-node-requirements \
        /opt/latentcrate-node-wheelhouse


# Restricted install stage: installs the locked third-party node wheels as an
# unprivileged user, without network access, into a separate package tree.
FROM comfy-python-base AS node-package-installer

ENV PIP_NO_CACHE_DIR=1

COPY config/python/custom-node-constraints.txt /etc/latentcrate/custom-node-constraints.txt
COPY services/comfy/install-node-deps.sh /usr/local/bin/latentcrate-install-node-deps

RUN chmod 0755 /usr/local/bin/latentcrate-install-node-deps \
    && mkdir -p /opt/latentcrate-node-site /opt/latentcrate-node-record \
    && chown -R 65534:65534 /opt/latentcrate-node-site /opt/latentcrate-node-record

USER 65534:65534

RUN --network=none \
    --mount=type=bind,from=node-deps-builder,source=/opt/latentcrate-node-wheelhouse,target=/tmp/latentcrate-node-wheelhouse,readonly \
    /usr/local/bin/latentcrate-install-node-deps \
        /tmp/latentcrate-node-wheelhouse \
        /opt/latentcrate-node-site \
        /opt/latentcrate-node-record


# Target without a frontend (the dist overlay serves one): combines the ComfyUI
# runtime with the isolated third-party node dependency tree. The frontend and
# Sage targets build from here.
FROM comfy-runtime-base AS comfy-runtime

ARG COMFY_CUDA_ARCH_LIST

LABEL io.latentcrate.custom-node.cuda-arch-list="${COMFY_CUDA_ARCH_LIST}"

COPY --from=node-package-installer --chown=0:0 \
    /opt/latentcrate-node-site /opt/latentcrate-node-site
COPY --from=node-package-installer --chown=0:0 \
    /opt/latentcrate-node-record/python-environment.id \
    /opt/latentcrate-node-record/python-environment.manifest \
    /usr/local/share/latentcrate/

# General C/C++ and Python build helpers inherited from the PyTorch runtime are
# retained for Triton/PyTorch JIT work. CUDA compilation and the reviewed wheel
# handoff remain build-stage concerns and must not leak into the runtime image.
RUN ! command -v nvcc \
    && test ! -e /usr/local/cuda/bin/nvcc \
    && test ! -d /opt/latentcrate-node-wheelhouse \
    && printf '%s\n' "${COMFY_CUDA_ARCH_LIST}" > /usr/local/share/latentcrate/custom-node.cuda-arch-list

ENV PYTHONPATH=/opt/latentcrate-node-site:/opt/comfyui


# -----------------------------------------------------------------------------
# Frontend and SageAttention targets
# -----------------------------------------------------------------------------

# Build-only: checks out one exact public frontend Git commit and builds its
# web files with the pinned Node and pnpm versions.
# The base image tag and its manifest digest are required build arguments
# (pins.env); the digest is what the build pulls.
# hadolint ignore=DL3006
FROM docker.io/library/node:${FRONTEND_NODE_TAG}@sha256:${FRONTEND_NODE_DIGEST} AS frontend-git-builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG FRONTEND_GIT_URL
ARG FRONTEND_GIT_REF
ARG FRONTEND_GIT_REQUESTED_REF
ARG FRONTEND_NODE_TAG
ARG FRONTEND_PNPM_VERSION

# Distribution packages track the pinned base image; per-package apt pins
# would drift from its snapshot and are not maintained here.
# hadolint ignore=DL3008
RUN --mount=type=cache,id=latentcrate-apt-debian-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=latentcrate-apt-debian-lists,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && npm install --global "pnpm@${FRONTEND_PNPM_VERSION}"

WORKDIR /frontend

# The Git config/env hardening below mirrors the canonical Bash copy in
# scripts/resolve-frontend.sh (hardened_git); keep the two aligned. The digest
# subshell requires cd, hence the DL3003 ignore.
# hadolint ignore=DL3003
RUN --mount=type=cache,id=latentcrate-frontend-pnpm,target=/root/.local/share/pnpm/store \
    printf '%s' "${FRONTEND_GIT_URL}" | grep -Eq '^https://[^/@[:space:]]+/[^?#[:space:]]+$' \
    && printf '%s' "${FRONTEND_GIT_REF}" | grep -Eq '^[0-9a-fA-F]{40}$' \
    && git init \
    && git -c credential.helper= -c core.askPass=/bin/false remote add origin "${FRONTEND_GIT_URL}" \
    && GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/false GIT_CONFIG_NOSYSTEM=1 \
       GIT_CONFIG_GLOBAL=/dev/null git -c credential.helper= \
       -c core.askPass=/bin/false -c http.followRedirects=false \
       fetch --depth=1 origin "${FRONTEND_GIT_REF}" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${FRONTEND_GIT_REF}" \
    && pnpm install --frozen-lockfile \
    && pnpm build \
    && test -r /frontend/dist/index.html \
    && mkdir -p /opt/latentcrate-frontend-build-info \
    && printf '%s\n' git > /opt/latentcrate-frontend-build-info/frontend.mode \
    && printf '%s\n' "${FRONTEND_GIT_URL}" > /opt/latentcrate-frontend-build-info/frontend.git-url \
    && printf '%s\n' "${FRONTEND_GIT_REQUESTED_REF}" > /opt/latentcrate-frontend-build-info/frontend.ref \
    && git rev-parse HEAD > /opt/latentcrate-frontend-build-info/frontend.commit \
    && printf '%s\n' "${FRONTEND_NODE_TAG}" > /opt/latentcrate-frontend-build-info/frontend.node-image \
    && printf '%s\n' "${FRONTEND_PNPM_VERSION}" > /opt/latentcrate-frontend-build-info/frontend.pnpm-version \
    && (export LC_ALL=C; cd /frontend/dist \
        && find . -type f -print0 \
            | sort -z \
            | xargs -0 sha256sum \
            | sha256sum \
            | cut -d ' ' -f 1) > /opt/latentcrate-frontend-build-info/frontend.content.sha256


# Target: adds the frontend built from Git. The default build passes the
# resolved commit of the pinned frontend tag; --frontend-git passes another.
FROM comfy-runtime AS runtime

ARG COMFYUI_FRONTEND_REF
ARG FRONTEND_GIT_URL
ARG FRONTEND_GIT_REF
ARG FRONTEND_GIT_REQUESTED_REF

LABEL io.latentcrate.frontend.mode="git" \
      io.latentcrate.comfyui-frontend.ref="${COMFYUI_FRONTEND_REF}" \
      io.latentcrate.frontend.git-url="${FRONTEND_GIT_URL}" \
      io.latentcrate.frontend.ref="${FRONTEND_GIT_REQUESTED_REF}" \
      io.latentcrate.frontend.commit="${FRONTEND_GIT_REF}"

COPY --from=frontend-git-builder /frontend/dist /opt/latentcrate-frontend
COPY --from=frontend-git-builder /opt/latentcrate-frontend-build-info/ /usr/local/share/latentcrate/

RUN test -r /opt/latentcrate-frontend/index.html \
    && rm -rf /opt/comfyui/web_custom_versions

ENV LATENTCRATE_FRONTEND_IMAGE_MODE=git


# Build-only: compiles the pinned SageAttention wheel for the selected GPU
# architecture. Final targets install it from a temporary read-only mount.
# The base image tag and its manifest digest are required build arguments
# (pins.env); the digest is what the build pulls.
# hadolint ignore=DL3006
FROM docker.io/pytorch/pytorch:${PYTORCH_DEVEL_TAG}@sha256:${PYTORCH_DEVEL_DIGEST} AS sage-builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG SAGEATTENTION_REF
ARG COMFY_CUDA_ARCH_LIST
ARG SAGE_BUILD_JOBS

ENV DEBIAN_FRONTEND=noninteractive \
    EXT_PARALLEL=1 \
    MAX_JOBS=${SAGE_BUILD_JOBS} \
    NVCC_APPEND_FLAGS="--threads ${SAGE_BUILD_JOBS}" \
    TORCH_CUDA_ARCH_LIST=${COMFY_CUDA_ARCH_LIST}

# Distribution packages track the pinned base image; per-package apt pins
# would drift from its snapshot and are not maintained here.
# hadolint ignore=DL3008
RUN --mount=type=cache,id=latentcrate-apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=latentcrate-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        git \
        ninja-build

RUN --mount=type=cache,id=latentcrate-pip-sage,target=/root/.cache/pip \
    git init /tmp/SageAttention \
    && git -C /tmp/SageAttention remote add origin https://github.com/thu-ml/SageAttention.git \
    && git -C /tmp/SageAttention fetch --depth 1 origin "${SAGEATTENTION_REF}" \
    && git -C /tmp/SageAttention checkout --detach FETCH_HEAD \
    && mkdir -p /wheels /opt/latentcrate-build-info \
    && python -m pip wheel \
        --no-build-isolation \
        --no-deps \
        --wheel-dir /wheels \
        /tmp/SageAttention \
    && printf '%s\n' "${SAGEATTENTION_REF}" > /opt/latentcrate-build-info/sageattention.ref \
    && git -C /tmp/SageAttention rev-parse HEAD > /opt/latentcrate-build-info/sageattention.commit \
    && printf '%s\n' "${COMFY_CUDA_ARCH_LIST}" > /opt/latentcrate-build-info/sageattention.cuda-arch-list \
    && sha256sum /wheels/*.whl \
        | cut -d ' ' -f 1 > /opt/latentcrate-build-info/sageattention.wheel.sha256 \
    && rm -rf /tmp/SageAttention


# Target: adds SageAttention to the Git-frontend image.
FROM runtime AS runtime-sage

ARG SAGEATTENTION_REF
ARG COMFY_CUDA_ARCH_LIST

ENV PYTHONPATH=/opt/comfyui

LABEL io.latentcrate.sageattention.enabled="true" \
      io.latentcrate.sageattention.ref="${SAGEATTENTION_REF}" \
      io.latentcrate.sage.cuda-arch-list="${COMFY_CUDA_ARCH_LIST}"

COPY --from=sage-builder /opt/latentcrate-build-info/ /usr/local/share/latentcrate/
COPY services/comfy/install-sage.sh /usr/local/bin/latentcrate-install-sage

RUN --mount=type=bind,from=sage-builder,source=/wheels,target=/tmp/sage-wheels,readonly \
    chmod 0755 /usr/local/bin/latentcrate-install-sage \
    && /usr/local/bin/latentcrate-install-sage \
    && rm -f /usr/local/bin/latentcrate-install-sage

ENV LATENTCRATE_SAGE_ENABLED=1 \
    PYTHONPATH=/opt/latentcrate-node-site:/opt/comfyui


# Default target: the Git-frontend image without SageAttention. The wrapper
# selects runtime-sage and comfy-runtime.
FROM runtime AS default
