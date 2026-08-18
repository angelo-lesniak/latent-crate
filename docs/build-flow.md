# Image build flow

The ComfyUI Dockerfile uses several stages so compilers, source trees, and
wheel files do not enter the final image. The build is split into four small
diagrams to keep the arrows short. Read each diagram from top to bottom.

Solid arrows mean `FROM`: the next stage builds on the earlier stage. Dotted
arrows pass only the named artifact. A stage may appear in more than one
diagram so each path can be read on its own.

## Shared ComfyUI runtime

```mermaid
flowchart TD
    RuntimeImage["PyTorch CUDA<br/>runtime image"] --> Python["comfy-python-base<br/>ComfyUI + Python packages"]
    Python --> RuntimeBase
    RuntimeBase["comfy-runtime-base<br/>Media + runtime tools"] --> Core["comfy-runtime<br/>Shared final-image core"]

    Devel["PyTorch CUDA<br/>development image"] --> Media["media-builder<br/>FFmpeg + codecs"]
    Devel --> NPP["torchcodec-runtime-libs<br/>Selected NPP libraries"]
    Media -. "media files" .-> RuntimeBase
    NPP -. "NPP libraries" .-> RuntimeBase

    NodePackages["node-package-installer<br/>Installed third-party node<br/>Python dependencies"] -. "isolated dependency tree" .-> Core
```

## Third-party node Python dependencies

```mermaid
flowchart TD
    Devel["PyTorch CUDA<br/>development image"] --> Builder["node-deps-builder<br/>Build missing dependency wheels"]
    Python["comfy-python-base<br/>Matching Python environment"] -. "environment + constraints" .-> Builder
    Python --> Installer["node-package-installer<br/>Install node dependencies offline"]
    Builder -. "temporary locked wheelhouse" .-> Installer
    Installer -. "isolated dependency tree" .-> Core["comfy-runtime"]
```

## Pinned release frontend

```mermaid
flowchart TD
    Core["comfy-runtime"] --> Release["runtime<br/>Pinned release frontend"]
    Release --> Sage["runtime-sage<br/>Release frontend + Sage"]
    Sage --> Default["default<br/>Alias to runtime-sage"]

    Devel["PyTorch CUDA<br/>development image"] --> SageBuilder["sage-builder<br/>Build SageAttention wheel"]
    SageBuilder -. "temporary wheel mount" .-> Sage
```

## Frontend built from Git

```mermaid
flowchart TD
    Core["comfy-runtime"] --> GitRuntime["runtime-frontend-git<br/>Git frontend"]
    GitRuntime --> GitSage["runtime-frontend-git-sage<br/>Git frontend + Sage"]

    NodeImage["Pinned Node image"] --> FrontendBuilder["frontend-git-builder<br/>Build web files"]
    FrontendBuilder -. "built web files" .-> GitRuntime

    SageBuilder["sage-builder output"] -. "temporary wheel mount" .-> GitSage
```

## Ready-to-run targets

| Target | Frontend | SageAttention |
|---|---|---:|
| `runtime` | Pinned release | No |
| `runtime-sage` | Pinned release | Yes |
| `runtime-frontend-git` | Exact public Git commit | No |
| `runtime-frontend-git-sage` | Exact public Git commit | Yes |
| `default` | Pinned release | Yes |

Local source mode and prebuilt `dist/` mode mount frontend files when the
container starts. They reuse the release image and do not add Dockerfile
stages.
