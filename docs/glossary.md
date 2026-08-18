# Glossary

This page explains terms used in LatentCrate. The definitions are short on
purpose.

- **Build stage:** a temporary part of a container build. Compiler tools can be
  used there without being copied into the final runtime image.
- **CDI:** Container Device Interface. LatentCrate uses CDI names such as
  `nvidia.com/gpu=all` to give a container access to NVIDIA GPUs.
- **ComfyUI Manager:** the extension that installs third-party nodes from inside
  the ComfyUI interface. The documentation calls it "Manager".
- **Compose:** the Docker or Podman tool that reads the project YAML files and
  builds, starts, and stops the services.
- **Compute capability:** NVIDIA's number for a GPU instruction architecture,
  such as `12.0`. Native CUDA code must include the target capability.
- **Container image:** the read-only packaged environment used to create a
  running container.
- **Custom node:** ComfyUI's name for third-party or local Python and JavaScript
  that extends ComfyUI. This documentation usually says "third-party node."
  These nodes are executable code and need the same trust as any application.
- **Dependency snapshot:** the saved copy of the third-party nodes' Python
  requirements under `build/custom-node-requirements`. The next image build
  installs the snapshot instead of installing packages at container start.
- **`dist/`:** the compiled files served as the ComfyUI web frontend.
- **Frontend mode:** where the web frontend comes from: a pinned release, public
  Git source, local source, or an existing `dist/` directory.
- **Node set:** a named group of third-party node repositories, pinned to exact
  commits, defined by a file under `config/custom-nodes/sets/` and managed
  with the `nodes` commands.
- **NPP:** NVIDIA Performance Primitives, a set of CUDA libraries for image and
  video processing. LatentCrate includes the required NPP runtime libraries only
  when TorchCodec is enabled.
- **NVCC:** NVIDIA's CUDA compiler. LatentCrate includes it only in build stages.
- **NVENC:** NVIDIA's hardware video encoder. The GPU smoke test performs a real
  short encode instead of only checking that FFmpeg lists the encoder.
- **Pinned version:** an exact selected release, tag, or commit. A pin prevents
  an automatic update, but a movable upstream tag is not a full supply-chain
  guarantee.
- **Profile:** a file under `versions/` that selects compatible ComfyUI,
  frontend, CUDA, PyTorch, FFmpeg, SageAttention, and build-tool versions.
- **Rootless Podman:** Podman running as the normal host user, not as root.
- **Sage-capable image:** an image with SageAttention compiled and installed.
  Workflows can use it without forcing ComfyUI's global Sage replacement.
- **Triton:** a compiler used by PyTorch for optimized GPU kernels. It is not the
  NVIDIA Triton inference server in this project.
- **Wrapper:** the `bin/latentcrate` script. It is the supported command-line
  interface; it validates settings and calls Compose with the correct files.
- **WSL2:** Windows Subsystem for Linux version 2. LatentCrate runs from its Linux
  shell and uses Linux containers; it does not run as a native Windows container.
