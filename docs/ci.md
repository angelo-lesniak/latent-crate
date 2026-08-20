# Continuous integration

The image workflow uses Buildx to build release, SageAttention, and trusted Git
frontend targets. These are compilation jobs: CUDA development images provide
NVCC, and explicit architecture lists let CUDA and SageAttention compile without
a physical NVIDIA GPU. The workflow does not test CUDA, Triton, Sage kernels,
TorchCodec CUDA, or NVENC at runtime.

The static workflow also renders every Compose variant with the pinned
standalone podman-compose parser. This catches provider-specific YAML and
profile handling without needing a Podman service in GitHub Actions.
It also builds the real model-set helper, downloads one tiny public pinned
file, and verifies that file again with networking disabled.

Standard GitHub-hosted `ubuntu-latest` runners may be too small for the CUDA
development/runtime bases, intermediate BuildKit state, and cache export. The
workflow leaves its result in BuildKit instead of loading a duplicate image into
the runner's Docker daemon. Set the repository variable
`LATENTCRATE_BUILD_RUNNER` to the label of a larger Linux runner or a self-hosted
CPU runner with ample disk. A GPU is not required for image construction. A
75 GB runner is a practical release-only starting point; prefer the 4-vCPU,
16-GB RAM, 150-GB class for Sage builds. Sage compilation is serialized across
extensions and uses the profile's low-memory `SAGE_BUILD_JOBS=2` setting;
increase it only on a runner with enough measured free memory.

GPU validation remains a separate manual process using the checked-in Arch or
WSL2 validation guide. If a GPU Actions job is added later, use a dedicated,
trusted self-hosted runner or a larger GPU-equipped runner, and do not expose it
to untrusted pull-request code or frontend/custom-node Git references.

The GitHub Actions cache only speeds up builds; it is not a record of how a
release was built. Its export is best-effort so quota or timeout pressure cannot
turn a successful image build into a failed job. Cache entries can be evicted,
so every target must continue to build correctly from a clean cache.

## Maintainer release checks

Before publishing images or describing a release as fully validated:

- run the public static and image-build workflows from a clean checkout;
- complete the relevant native NVIDIA or WSL2 GPU validation guide;
- review Git references and pin base images by digest when reproducible image
  publication requires it;
- generate an SBOM and review the vulnerability scan;
- review third-party notices, source-offer duties, and other redistribution
  requirements;
- retain the image digest, validation reports, frontend source details, and any
  accepted exceptions with the release.

GitHub private vulnerability reporting should be enabled when the repository is
published. Buildx cache reuse may be checked for build performance, but it is
not evidence that an image works correctly.
