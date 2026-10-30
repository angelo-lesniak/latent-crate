#!/usr/bin/env python3
"""Repository-level checks that do not require a container engine.

Structured invariants (Compose services, workflow steps) are asserted on
parsed YAML so harmless reformatting and anchor/merge refactors cannot break
them. Raw-text checks remain only for artifacts that truly are text
(Dockerfile stage graph, shell scripts, .env examples). Each check function
enforces one named contract and fails with the rule, not just a missing
string.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

try:
    import yaml
except ImportError:
    raise SystemExit(
        "project check: PyYAML is required: python3 -m pip install -r requirements-dev.txt\n"
        "(a silent skip would hollow out this gate, so the dependency is mandatory)"
    )


ROOT = Path(__file__).resolve().parents[1]

# CUDA_MIN_DRIVER_MAJOR is a host-side doctor input, not an image build input,
# so it is exempt from the build-argument rules in check_pins_single_source.
NON_BUILD_PIN_KEYS = {"CUDA_MIN_DRIVER_MAJOR"}
CLI_MODULES = (
    "lib/latentcrate/core.sh",
    "lib/latentcrate/frontend.sh",
    "lib/latentcrate/node-deps.sh",
    "lib/latentcrate/nodes.sh",
    "lib/latentcrate/models.sh",
    "lib/latentcrate/templates.sh",
    "lib/latentcrate/runtime.sh",
)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\((?P<target>[^)]+)\)")


def fail(message: str) -> None:
    raise SystemExit(f"project check: {message}")


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def load_yaml(relative: str) -> dict:
    document = yaml.safe_load(read_text(relative))
    if not isinstance(document, dict):
        fail(f"{relative} did not parse to a YAML mapping")
    return document


def check_yaml_duplicate_keys() -> None:
    """Literal YAML mappings must not silently overwrite an earlier key."""

    def walk(node: yaml.Node, relative: Path, path: tuple[str, ...] = ()) -> None:
        if isinstance(node, yaml.MappingNode):
            seen: set[str] = set()
            for key_node, value_node in node.value:
                key = key_node.value if isinstance(key_node, yaml.ScalarNode) else "<complex>"
                if key != "<<" and key in seen:
                    location = ".".join((*path, key))
                    fail(f"duplicate YAML key in {relative}: {location}")
                seen.add(key)
                walk(value_node, relative, (*path, key))
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                walk(item, relative, (*path, str(index)))

    paths = sorted(ROOT.glob("compose*.yaml"))
    paths.extend(sorted((ROOT / ".github" / "workflows").glob("*.yml")))
    for path in paths:
        node = yaml.compose(path.read_text(encoding="utf-8"))
        if node is not None:
            walk(node, path.relative_to(ROOT))


def parse_env(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines, tolerating blank lines and # comments."""
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            fail(f"invalid environment line in {path}: {raw_line}")
        key, value = line.split("=", 1)
        if key in values:
            fail(f"duplicate key {key} in {path}")
        values[key] = value
    return values


def walk_strings(node):
    """Yield every string scalar in a parsed YAML tree."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from walk_strings(key)
            yield from walk_strings(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from walk_strings(item)


def compose_document() -> dict:
    return load_yaml("compose.yaml")


def compose_service(compose: dict, name: str) -> dict:
    service = compose.get("services", {}).get(name)
    if not isinstance(service, dict):
        fail(f"compose.yaml must define the {name} service")
    return service


def require_service_hardening(name: str, service: dict, *, network_none: bool = False) -> None:
    """A helper/runtime container must run read-only, without capabilities,
    and without privilege escalation (and offline where required)."""
    if service.get("read_only") is not True:
        fail(f"service {name} must set read_only: true; helper and runtime containers run on a read-only root filesystem")
    if "ALL" not in (service.get("cap_drop") or []):
        fail(f"service {name} must drop all Linux capabilities (cap_drop: [\"ALL\"])")
    if "no-new-privileges:true" not in (service.get("security_opt") or []):
        fail(f"service {name} must set security_opt no-new-privileges:true so processes cannot gain privileges via setuid/exec")
    if network_none and service.get("network_mode") != "none":
        fail(f"service {name} must run with network_mode: none; it performs offline work only")


def find_mount(service: dict, target: str) -> dict | None:
    for mount in service.get("volumes") or []:
        if isinstance(mount, dict) and mount.get("target") == target:
            return mount
    return None


def command_has_pair(command: list, flag: str, value: str) -> bool:
    for index, item in enumerate(command[:-1]):
        if item == flag and command[index + 1] == value:
            return True
    return False


# --- CLI structure -----------------------------------------------------------


def read_cli_sources() -> str:
    """The CLI dispatcher must load only fixed repository modules, and the
    extracted implementations must not creep back into the entrypoint."""
    entrypoint = read_text("bin/latentcrate")
    sources = [entrypoint]
    for relative in CLI_MODULES:
        path = ROOT / relative
        if not path.is_file():
            fail(f"CLI module is missing: {relative}")
        if f'source "$PROJECT_ROOT/{relative}"' not in entrypoint:
            fail(f"CLI does not load its fixed repository module: {relative}")
        sources.append(path.read_text(encoding="utf-8"))
    for implementation in (
        "compose()",
        "prepare_frontend_mode()",
        "snapshot_node_dependencies()",
        "run_node_set()",
        "run_model_sets()",
        "run_gpu_smoke()",
    ):
        if implementation in entrypoint:
            fail(f"CLI entrypoint still contains extracted implementation: {implementation}")
    return "\n".join(sources)


# --- Pins --------------------------------------------------------------------


def check_pins_single_source() -> None:
    """pins.env is the only place a pinned value may live: every pin is a
    Dockerfile ARG and a required comfy build argument, no Dockerfile ARG
    carries a default, no Compose file gives a pin a fallback, the image and
    target come from the wrapper, and every base image is pulled by its
    pinned tag and digest."""
    pins = parse_env(ROOT / "pins.env")
    build_pins = set(pins) - NON_BUILD_PIN_KEYS
    dockerfile = read_text("services/comfy/Dockerfile")
    for match in re.finditer(r"^ARG ([A-Z0-9_]+)=", dockerfile, re.MULTILINE):
        fail(f"Dockerfile ARG {match.group(1)} has a default; build inputs come from pins.env and .env only")
    declared = set(re.findall(r"^ARG ([A-Z0-9_]+)$", dockerfile, re.MULTILINE))
    for key in sorted(build_pins - declared):
        fail(f"pin {key} is not declared as a Dockerfile build argument")
    for match in re.finditer(r"^FROM (\S+)", dockerfile, re.MULTILINE):
        image = match.group(1)
        if "/" in image and not re.fullmatch(
            r"\S+:\$\{[A-Z0-9_]+_TAG\}@sha256:\$\{[A-Z0-9_]+_DIGEST\}", image
        ):
            fail(f"base image must be pulled by a pinned tag and digest: {image}")

    compose = compose_document()
    comfy = compose_service(compose, "comfy")
    if comfy.get("image") != "${LATENTCRATE_IMAGE:?set by bin/latentcrate}":
        fail("the comfy service image must be the reference bin/latentcrate derives, without a fallback")
    if comfy.get("build", {}).get("target") != "${COMFY_BUILD_TARGET:?set by bin/latentcrate}":
        fail("the comfy service build target must be the one bin/latentcrate selects, without a fallback")
    build_args = comfy.get("build", {}).get("args") or {}
    for key in sorted(build_pins):
        if build_args.get(key) != f"${{{key}:?pin it in pins.env}}":
            fail(f"the comfy service must pass pin {key} as a required build argument")
    for path in sorted(ROOT.glob("compose*.yaml")):
        for value in walk_strings(load_yaml(path.name)):
            for match in re.finditer(r"\$\{([A-Z0-9_]+):-([^}]*)\}", value):
                if match.group(1) in pins and match.group(2):
                    fail(f"pin {match.group(1)} has a Compose fallback in {path.name}: {match.group(0)}")
    for relative in (
        "bin/latentcrate",
        *sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "lib/latentcrate").glob("*.sh")),
        "services/comfy/Dockerfile",
    ):
        for match in re.finditer(r"\$\{([A-Z0-9_]+):-", read_text(relative)):
            if match.group(1) in pins:
                fail(f"pin {match.group(1)} has a fallback in {relative}: {match.group(0)}")


def check_pin_families() -> None:
    """The pins that name a CUDA family or a PyTorch release independently
    must agree: the +cuNNN suffix of TORCHCODEC_VERSION and the major of
    CUDA_NPP_VERSION follow the cudaNN.N of PYTORCH_RUNTIME_TAG, and
    PYTORCH_DEVEL_TAG names the same release."""
    pins = parse_env(ROOT / "pins.env")
    cuda = re.search(r"-cuda(\d+)\.(\d+)-", pins.get("PYTORCH_RUNTIME_TAG", ""))
    if (
        cuda is None
        or pins.get("TORCHCODEC_VERSION", "").rpartition("+")[2] != f"cu{cuda.group(1)}{cuda.group(2)}"
        or pins.get("CUDA_NPP_VERSION", "").split(".", 1)[0] != cuda.group(1)
        or pins.get("PYTORCH_DEVEL_TAG", "").removesuffix("-devel")
        != pins["PYTORCH_RUNTIME_TAG"].removesuffix("-runtime")
    ):
        fail(
            "PYTORCH_DEVEL_TAG, PYTORCH_RUNTIME_TAG, TORCHCODEC_VERSION, and CUDA_NPP_VERSION in "
            "pins.env must name one PyTorch release and CUDA family; change the four pins together"
        )


# --- Dockerfile stage graph (deliberately text-based: the artifact is text) --


def check_dockerfile_stage_graph() -> None:
    """The image exposes three targets with the documented inheritance: the
    frontend target builds on the frontend-less runtime, the Sage target on
    the frontend target, and a plain build gives the frontend target."""
    dockerfile = read_text("services/comfy/Dockerfile")
    for edge in (
        "FROM comfy-runtime-base AS comfy-runtime",
        "FROM comfy-runtime AS runtime",
        "FROM runtime AS runtime-sage",
        "FROM runtime AS default",
    ):
        if not re.search(rf"^{re.escape(edge)}$", dockerfile, re.MULTILINE):
            fail(f"Dockerfile stage graph misses: {edge}")


def check_dockerfile_cache_decoupling() -> None:
    """The shared runtime layers (comfy-python-base up to comfy-runtime-base)
    must not reference frontend or Sage pins, so changing those pins cannot
    invalidate the common build cache."""
    dockerfile = read_text("services/comfy/Dockerfile")
    common_runtime = re.split(r"^FROM .+ AS comfy-python-base$", dockerfile, maxsplit=1, flags=re.MULTILINE)[1]
    common_runtime = common_runtime.split("FROM comfy-python-base AS comfy-runtime-base", 1)[0]
    for unrelated_pin in ("COMFYUI_FRONTEND_REF", "SAGEATTENTION_REF", "COMFY_CUDA_ARCH_LIST"):
        if unrelated_pin in common_runtime:
            fail(f"common runtime cache is coupled to unrelated pin: {unrelated_pin}")


def check_runtime_stage_arg_scope() -> None:
    """Buildah and Docker must receive every build argument used by the
    comfy-runtime-base child stage, including its TorchCodec branch and
    provenance labels."""
    dockerfile = read_text("services/comfy/Dockerfile")
    runtime_base = dockerfile.split(
        "FROM comfy-python-base AS comfy-runtime-base", 1
    )[1].split("\nFROM ", 1)[0]
    for name in (
        "COMFYUI_REF",
        "CUDA_NPP_VERSION",
        "FFMPEG_REF",
        "NV_CODEC_HEADERS_REF",
        "SVT_AV1_REF",
        "TORCHCODEC_VERSION",
    ):
        if not re.search(rf"^ARG {re.escape(name)}$", runtime_base, re.MULTILINE):
            fail(f"comfy-runtime-base must redeclare build argument: {name}")


# --- Frontend modes ----------------------------------------------------------


def check_frontend_runtime_contract() -> None:
    """The runtime must always launch ComfyUI with an explicit frontend root
    so the effective frontend is the one selected at build/run time."""
    if "--front-end-root" not in read_text("services/comfy/entrypoint.sh"):
        fail("the runtime must launch ComfyUI through an explicit --front-end-root")


def check_dist_frontend_overlay() -> None:
    """The frontend-dist overlay must exist, mount the dist read-only, and
    use the entrypoint's fixed mount path."""
    overlay = ROOT / "compose.frontend-dist.yaml"
    overlay_text = overlay.read_text(encoding="utf-8") if overlay.is_file() else ""
    if "read_only: true" not in overlay_text:
        fail("frontend-dist overlay must exist and mount the built frontend read-only")
    if "/opt/latentcrate-frontend-dist" not in overlay_text:
        fail("frontend-dist overlay must use the entrypoint's fixed mount path /opt/latentcrate-frontend-dist")


def check_frontend_tooling() -> None:
    """Local frontend source builds must run containerized, with the online
    fetch and offline install/build phases separated."""
    cli = read_cli_sources()
    if "--frontend-source" not in cli:
        fail("the CLI must expose containerized local frontend source builds (--frontend-source)")

    tools_dockerfile = ROOT / "services/tools/Dockerfile"
    builder = ROOT / "services/tools/build-local-frontend.sh"
    if not tools_dockerfile.is_file() or not builder.is_file():
        fail("containerized local frontend tool files are missing")
    builder_text = builder.read_text(encoding="utf-8")
    for required in (
        "--config.pm-on-fail=ignore install",
        "PNPM_CONFIG_PM_ON_FAIL=ignore",
        "--ignore-scripts",
        "--offline",
        "--trust-lockfile",
        "pnpm build",
        "--exclude='./.git'",
        "--exclude='*/node_modules'",
    ):
        if required not in builder_text:
            fail(f"local frontend builder misses hardened phase: {required}")
    if "pnpm fetch" in builder_text:
        fail("local frontend preparation must resolve all metadata with an online script-free install")


def check_frontend_helper_services() -> None:
    """frontend-fetch may reach the network but must not see the generated
    output; frontend-build gets the output but must run offline. Both must be
    hardened containers."""
    compose = compose_document()
    fetch = compose_service(compose, "frontend-fetch")
    build = compose_service(compose, "frontend-build")
    require_service_hardening("frontend-fetch", fetch)
    require_service_hardening("frontend-build", build, network_none=True)
    if find_mount(fetch, "/output") is not None:
        fail("the networked frontend-fetch service must not receive the generated-output mount at /output")
    if find_mount(build, "/output") is None:
        fail("the offline frontend-build service must receive the generated-output mount at /output")



# --- Build and runtime hardening ---------------------------------------------


def check_node_deps_build_isolation() -> None:
    """The copied requirement tree is rewritten and compiled in a dedicated
    CUDA build stage; the runtime consumes only the CUDA-built packages."""
    dockerfile = read_text("services/comfy/Dockerfile")
    builder = re.split(
        r"^FROM \S+ AS node-deps-builder$", dockerfile, maxsplit=1, flags=re.MULTILINE
    )
    if len(builder) != 2 or not re.search(
        r"^FROM docker\.io/pytorch/pytorch:\$\{PYTORCH_DEVEL_TAG\}@sha256:\$\{PYTORCH_DEVEL_DIGEST\} AS node-deps-builder$",
        dockerfile,
        re.MULTILINE,
    ):
        fail("third-party node wheels must build in the pinned CUDA development image (node-deps-builder stage)")
    builder_stage = re.split(r"^FROM ", builder[1], maxsplit=1, flags=re.MULTILINE)[0]
    if dockerfile.count("COPY build/custom-node-requirements") != 1 or "COPY build/custom-node-requirements" not in builder_stage:
        fail("the requirement tree must enter the node-deps-builder stage only")
    rewrite = builder_stage.find("rewrite-node-requirements.py /tmp/custom-node-requirements")
    build = builder_stage.find("&& /usr/local/bin/latentcrate-build-node-deps")
    if rewrite < 0 or build < 0 or rewrite > build:
        fail("node-deps-builder must rewrite the requirement tree before building wheels from it")
    if "from=node-deps-builder" not in dockerfile:
        fail("the runtime must consume the CUDA-built third-party node packages from node-deps-builder")
    if "comfy-environment-constraints.txt" not in dockerfile:
        fail("custom-node resolution must use the resolved ComfyUI environment constraints")
    if "test ! -e /usr/local/cuda/bin/nvcc" not in dockerfile:
        fail("the final runtime must assert that the CUDA compiler stays in build stages")
    if "test ! -d /opt/latentcrate-node-wheelhouse" not in dockerfile:
        fail("the final runtime must assert that the third-party node wheelhouse stays in build stages")


def check_node_deps_offline_install() -> None:
    """The final third-party node package install must be offline, unprivileged,
    and consume only the locally built package lock."""
    dockerfile = read_text("services/comfy/Dockerfile")
    installer = read_text("services/comfy/install-node-deps.sh")
    for local_install_contract in (
        "install-requirements.txt",
        "--no-deps",
        "--no-index",
        "--only-binary=:all:",
    ):
        if local_install_contract not in installer:
            fail(f"custom-node installation must be lock-driven and offline; missing: {local_install_contract}")
    if "git+" in installer or "manifest.txt" in installer:
        fail("the final custom-node install must consume only the locally built package lock, not Git URLs or the raw manifest")
    final_install = dockerfile.split("RUN --network=none", 1)
    if len(final_install) != 2 or "latentcrate-install-node-deps" not in final_install[1]:
        fail("the final third-party node package install must run with networking disabled (RUN --network=none)")
    if "USER 65534:65534" not in dockerfile or '--target "$target"' not in installer:
        fail("third-party node packages must install as an unprivileged user into an isolated --target directory")


def check_node_wheel_builder() -> None:
    """The custom-node CUDA wheel builder must exist, build wheels, and
    reject symbolic links in its output."""
    wheel_builder = ROOT / "services/comfy/build-node-deps.sh"
    if not wheel_builder.is_file():
        fail("the custom-node CUDA wheel builder is missing")
    builder_text = wheel_builder.read_text(encoding="utf-8")
    if "python -m pip wheel" not in builder_text:
        fail("the custom-node CUDA wheel builder must build wheels via python -m pip wheel")
    if "symbolic links in the wheel directory" not in builder_text:
        fail("third-party node wheel output must reject symbolic links")


def check_runtime_python_environment() -> None:
    """Runtime Python dependencies live in an isolated virtual environment
    with reviewed constraints; system protections must stay intact and the
    durable home user-site must stay off the image PATH."""
    dockerfile = read_text("services/comfy/Dockerfile")
    if "PIP_NO_BUILD_ISOLATION" in dockerfile:
        fail("build isolation must not be disabled globally")
    if "/data/home/.local/bin" in dockerfile:
        fail("the durable home user-site must not be on the image PATH")
    if "python3-venv" not in dockerfile or "-m venv --system-site-packages /opt/latentcrate-venv" not in dockerfile:
        fail("runtime dependencies must install into an isolated virtual environment")
    if "PATH=/opt/latentcrate-venv/bin:" not in dockerfile:
        fail("the runtime virtual environment must be the default Python environment")
    if "--break-system-packages" in dockerfile:
        fail("the runtime must not bypass externally-managed Python protections")
    if "runtime-constraints.txt >> /etc/latentcrate/core-constraints.txt" not in dockerfile:
        fail("reviewed runtime constraints must also constrain custom-node installs")
    runtime_constraints = read_text("config/python/runtime-constraints.txt")
    if "click>=8,<8.4,!=8.3.0" not in runtime_constraints:
        fail("runtime constraints must preserve the pinned PyTorch spin/Click compatibility")
    if "chardet>=5,<6" not in runtime_constraints:
        fail("runtime constraints must preserve Requests/Chardet compatibility")
    native_node_packages = (
        "libgl1",
        "libjpeg-turbo8",
        "libopenjp2-7",
        "libsm6",
        "libtiff6",
        "libwebp7",
        "libxfixes3",
        "libxrender1",
    )
    for package in native_node_packages:
        if not re.search(rf"^\s+{re.escape(package)}\s+\\$", dockerfile, re.MULTILINE):
            fail(f"the runtime must provide tested third-party node package {package}")

    replacements = tomllib.loads(
        read_text("config/python/custom-node-package-replacements.toml")
    )
    expected_opencv_replacements = {
        "opencv-python": "opencv-contrib-python",
        "opencv-python-headless": "opencv-contrib-python",
        "opencv-contrib-python-headless": "opencv-contrib-python",
    }
    actual_opencv_replacements = {
        replacement.get("from"): replacement.get("to")
        for replacement in replacements.get("replacement", [])
    }
    if actual_opencv_replacements != expected_opencv_replacements:
        fail("the package replacement policy must select one OpenCV cv2 provider")


def check_media_stack() -> None:
    """The custom FFmpeg build must expose shared libraries and x265, and the
    media/NPP library paths must preserve the inherited NVIDIA paths."""
    dockerfile = read_text("services/comfy/Dockerfile")
    if "--enable-libx265" not in dockerfile or "--enable-shared" not in dockerfile:
        fail("the custom FFmpeg build must expose shared libraries and x265")
    if (
        "LD_LIBRARY_PATH=/opt/latentcrate-media/lib:/opt/latentcrate-npp/lib:${LD_LIBRARY_PATH}"
        not in dockerfile
    ):
        fail("the media and NPP library paths must preserve inherited NVIDIA library paths")


def check_torchcodec_pinning() -> None:
    """TorchCodec must install from the pinned version and extract
    its pinned CUDA NPP runtime dependencies from the CUDA devel image."""
    dockerfile = read_text("services/comfy/Dockerfile")
    if "torchcodec==${TORCHCODEC_VERSION}" not in dockerfile:
        fail("TorchCodec must be installed from the pinned TORCHCODEC_VERSION")
    if not re.search(
        r"^FROM docker\.io/pytorch/pytorch:\$\{PYTORCH_DEVEL_TAG\}@sha256:\$\{PYTORCH_DEVEL_DIGEST\} AS torchcodec-runtime-libs$",
        dockerfile,
        re.MULTILINE,
    ):
        fail("TorchCodec must extract its pinned CUDA NPP runtime dependencies (torchcodec-runtime-libs stage)")
    if "for library in nppc nppicc" not in dockerfile:
        fail("TorchCodec runtime extraction must cover both NPP libraries (nppc and nppicc)")
    if "lib${library}.so.${CUDA_NPP_VERSION}" not in dockerfile:
        fail("TorchCodec NPP extraction must copy the exact pinned CUDA_NPP_VERSION sonames")


def check_image_metadata() -> None:
    """The aggregate image must not misstate its licensing, must prepare its
    build-record directory, and must not disclose detailed custom-node
    metadata records in the final image."""
    dockerfile = read_text("services/comfy/Dockerfile")
    if 'org.opencontainers.image.licenses="MIT"' in dockerfile:
        fail("the aggregate third-party image must not be labelled as MIT-only")
    if "mkdir -p /usr/local/share/latentcrate" not in dockerfile:
        fail("runtime build-record directory must exist before its files are written")
    for private_record in (
        "custom-node-requirements.sha256",
        "custom-node-packages.txt",
        "custom-node-build-environment.txt",
    ):
        if f"/usr/local/share/latentcrate/{private_record}" in dockerfile:
            fail(f"final image discloses detailed custom-node metadata: {private_record}")


def check_manager_defaults() -> None:
    """The shipped ComfyUI Manager configuration must keep every protective
    setting, and the entrypoint must honor both supported user config
    locations."""
    manager_template = read_text("config/comfy/manager-config.ini")
    for manager_setting in (
        "security_level = normal",
        "downgrade_blacklist = torch, torchvision, torchaudio, triton",
        "share_option = none",
        "network_mode = public",
        "http_channel_enabled = false",
        "bypass_ssl = false",
        "allow_git_url_install = false",
        "allow_pip_install = false",
        "model_download_by_agent = false",
        "default_cache_as_channel_url = false",
        "file_logging = false",
    ):
        if manager_setting not in manager_template:
            fail(f"Manager protection is missing: {manager_setting}")
    entrypoint = read_text("services/comfy/entrypoint.sh")
    if "user/__manager/config.ini" not in entrypoint or "alternate_config" not in entrypoint:
        fail("Manager defaults must preserve both supported user configuration paths")


def check_entrypoint_privacy() -> None:
    """API-node blocking is an explicit strict-mode opt-in and metadata remains
    an opt-in; both toggles must be wired through to ComfyUI arguments."""
    entrypoint = read_text("services/comfy/entrypoint.sh")
    for privacy_contract in (
        "COMFY_DISABLE_API_NODES:-false",
        "args+=(--disable-api-nodes)",
        "COMFY_DISABLE_METADATA:-false",
        "args+=(--disable-metadata)",
    ):
        if privacy_contract not in entrypoint:
            fail(f"runtime privacy contract is missing: {privacy_contract}")


def check_entrypoint_sage_contract() -> None:
    """The global Sage mode must map to ComfyUI's --use-sage-attention and
    stay guarded by the Sage-capable image marker."""
    entrypoint = read_text("services/comfy/entrypoint.sh")
    for sage_contract in (
        "LATENTCRATE_SAGE_MODE:-off",
        "args+=(--use-sage-attention)",
        "LATENTCRATE_SAGE_ENABLED:-0",
    ):
        if sage_contract not in entrypoint:
            fail(f"runtime Sage contract is missing: {sage_contract}")

    gpu_smoke = read_text("services/comfy/gpu-smoke.sh")
    if 'find_spec("sageattention") is not None' not in gpu_smoke:
        fail("GPU verification must prove SageAttention is absent from the default image")


def check_comfy_service_hardened() -> None:
    """The ComfyUI runtime container must be contained (read-only root, no
    capabilities, no privilege escalation, PID limit, bounded tmpfs), mount
    local-only custom nodes read-only, and carry the privacy defaults."""
    compose = compose_document()
    comfy = compose_service(compose, "comfy")
    require_service_hardening("comfy", comfy)
    if not comfy.get("pids_limit"):
        fail("the comfy service must set pids_limit to bound fork bombs")
    tmpfs_entries = comfy.get("tmpfs") or []
    if not any(
        isinstance(entry, str) and entry.startswith("/tmp:rw,nosuid,nodev,mode=1777,size=")
        for entry in tmpfs_entries
    ):
        fail("the comfy service /tmp must be a bounded nosuid,nodev tmpfs (/tmp:rw,nosuid,nodev,mode=1777,size=...)")
    local_nodes = find_mount(comfy, "/local/custom_nodes")
    if local_nodes is None or local_nodes.get("read_only") is not True:
        fail("local-only custom nodes must be mounted read-only at /local/custom_nodes")
    environment = comfy.get("environment") or {}
    for variable, setting in (
        ("COMFY_DISABLE_API_NODES", "COMFY_DISABLE_API_NODES"),
        ("COMFY_DISABLE_METADATA", "COMFY_DISABLE_METADATA"),
        ("LATENTCRATE_SAGE_MODE", "LATENTCRATE_SAGE"),
    ):
        if not re.fullmatch(rf"\$\{{{setting}:\?[^}}]*\}}", str(environment.get(variable, ""))):
            fail(f"ComfyUI environment must pass {variable} through from the required setting {setting}")


def check_env_example_defaults() -> None:
    """The environment template and CLI must carry the documented defaults."""
    env_example = read_text(".env.example")
    for default in (
        "COMFY_DISABLE_API_NODES=false",
        "COMFY_DISABLE_METADATA=false",
        "COMFY_ALLOW_REMOTE=false",
        "COMFY_PIDS_LIMIT=2048",
        "COMFY_LOCAL_NODES_DIR=./local/custom_nodes",
        "LATENTCRATE_SAGE=off",
    ):
        if default not in env_example:
            fail(f"privacy/containment default is undocumented: {default}")

    cli = read_text("bin/latentcrate")
    if "local value=${LATENTCRATE_SAGE:-off}" not in cli:
        fail("the CLI must default LATENTCRATE_SAGE to off when no setting exists")


def check_excluded_default_features() -> None:
    """Features deliberately excluded from the default image must not
    reappear."""
    dockerfile = read_text("services/comfy/Dockerfile")
    for excluded in ("bootstrap-assets", "fonts-noto", "taesd"):
        if excluded in dockerfile.lower():
            fail(f"excluded default feature is present: {excluded}")
    if re.search(r"^\s*sox(?:\s|\\|$)", dockerfile, re.MULTILINE | re.IGNORECASE):
        fail("SoX must not be added to the default image")


def check_node_deps_snapshot_service() -> None:
    """The snapshot helper container must be hardened and offline, scan both
    managed and local sources, and receive the pin/allowlist configuration
    read-only."""
    compose = compose_document()
    snapshot = compose_service(compose, "node-deps-snapshot")
    require_service_hardening("node-deps-snapshot", snapshot, network_none=True)
    command = snapshot.get("command") or []
    for source in ("/input/managed", "/input/local"):
        if not command_has_pair(command, "--source", source):
            fail(f"the custom-node snapshot must scan {source} (--source {source})")
    for option, config_target in (
        ("--vcs-pins", "/config/vcs-pins.toml"),
        (
            "--package-replacements",
            "/config/custom-node-package-replacements.toml",
        ),
        ("--allowed-git-hosts", "/config/allowed-git-hosts.txt"),
    ):
        if not command_has_pair(command, option, config_target):
            fail(f"the custom-node snapshot must receive {option} {config_target}")
        mount = find_mount(snapshot, config_target)
        if mount is None or mount.get("read_only") is not True:
            fail(f"the custom-node snapshot must receive {config_target} as a read-only mount")


def check_node_set_services_hardened() -> None:
    """The node-set installer and status containers must be hardened; the
    status variant must additionally be offline with a read-only /nodes
    mount, while the installer keeps /nodes writable."""
    compose = compose_document()
    node_set = compose_service(compose, "node-set")
    node_set_status = compose_service(compose, "node-set-status")
    require_service_hardening("node-set", node_set)
    require_service_hardening("node-set-status", node_set_status, network_none=True)
    for name, service in (("node-set", node_set), ("node-set-status", node_set_status)):
        manifest = find_mount(service, "/config/node-set.toml")
        if manifest is None or manifest.get("read_only") is not True:
            fail(f"service {name} must mount the node-set manifest read-only at /config/node-set.toml")
        hosts = find_mount(service, "/config/allowed-git-hosts.txt")
        if hosts is None or hosts.get("read_only") is not True:
            fail(f"service {name} must mount the Git host allowlist read-only")
        nodes = find_mount(service, "/nodes")
        if nodes is None:
            fail(f"service {name} must mount the target custom-node directory at /nodes")
    if find_mount(node_set, "/nodes").get("read_only") is True:
        fail("the node-set installer needs a writable /nodes mount to install and sync sets")
    if find_mount(node_set_status, "/nodes").get("read_only") is not True:
        fail("node-set status must inspect /nodes read-only; status must never modify installed nodes")


def check_model_set_services_hardened() -> None:
    """Model fetching must use a hardened networked helper, while status is
    offline and read-only. Tokens must not be part of Compose configuration."""
    compose = compose_document()
    fetch = compose_service(compose, "model-set")
    status = compose_service(compose, "model-set-status")
    require_service_hardening("model-set", fetch)
    require_service_hardening("model-set-status", status, network_none=True)
    for name, service in (("model-set", fetch), ("model-set-status", status)):
        manifests = find_mount(service, "/config/model-sets")
        if manifests is None or manifests.get("read_only") is not True:
            fail(f"service {name} must receive model-set manifests read-only")
        models = find_mount(service, "/models")
        if models is None:
            fail(f"service {name} must mount the shared models directory")
    if find_mount(fetch, "/models").get("read_only") is True:
        fail("model-set fetch needs a writable /models mount")
    fetch_cache = find_mount(fetch, "/cache")
    if fetch_cache is None or not str(fetch_cache.get("source", "")).endswith("/huggingface"):
        fail("model-set fetch must receive only the Hugging Face sub-cache")
    if find_mount(status, "/models").get("read_only") is not True:
        fail("model-set status must inspect /models read-only")
    if find_mount(status, "/cache") is not None:
        fail("model-set status must not receive the download cache")
    if "HF_TOKEN" in read_text("compose.yaml"):
        fail("HF_TOKEN must be piped to the download helper, never placed in Compose configuration")
    docker_services = load_yaml("compose.docker.yaml").get("services", {})
    podman_services = load_yaml("compose.podman.yaml").get("services", {})
    for name in ("model-set", "model-set-status"):
        if "${HOST_MODEL_GID:-1000}" not in docker_services.get(name, {}).get("group_add", []):
            fail(f"Docker service {name} must receive the model-storage group")
        if "keep-groups" not in podman_services.get(name, {}).get("group_add", []):
            fail(f"Podman service {name} must preserve supplementary model-storage groups")


def check_template_services_hardened() -> None:
    """Template inspection must use the selected image offline, while only
    draft creation may write to the narrow draft output mount."""
    compose = compose_document()
    inspector = compose_service(compose, "template-inspector")
    draft = compose_service(compose, "template-draft")
    require_service_hardening("template-inspector", inspector, network_none=True)
    require_service_hardening("template-draft", draft, network_none=True)
    if inspector.get("image") != compose_service(compose, "comfy").get("image"):
        fail("template inspection must use the exact selected ComfyUI image")
    if find_mount(inspector, "/output") is not None:
        fail("template listing must not receive a writable output mount")
    output = find_mount(draft, "/output")
    if output is None or output.get("read_only") is True:
        fail("template draft creation needs one writable /output mount")
    if "/usr/local/bin/latentcrate-manage-templates.py" not in read_text(
        "services/comfy/Dockerfile"
    ):
        fail("the selected ComfyUI image must contain the template inspector")


def check_model_set_manifests_pinned() -> None:
    """Every shipped model file and workflow reference must be immutable and
    carry enough metadata for size and SHA-256 verification."""
    manifest_paths = sorted((ROOT / "config" / "model-sets").glob("*.toml"))
    if not manifest_paths:
        fail("at least one model-set manifest is required")
    for path in manifest_paths:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        workflows = document.get("workflow_urls")
        if not isinstance(workflows, list) or not workflows:
            fail(f"model-set has no workflow links: {path.relative_to(ROOT)}")
        for workflow in workflows:
            if not re.fullmatch(
                r"https://github\.com/Comfy-Org/workflow_templates/blob/"
                r"[0-9a-f]{40}/templates/[A-Za-z0-9_.-]+\.json",
                str(workflow),
            ):
                fail(f"model-set workflow URL is not commit-pinned: {path.relative_to(ROOT)}")
        licenses = document.get("license")
        if not isinstance(licenses, list) or not licenses:
            fail(f"model-set has no license references: {path.relative_to(ROOT)}")
        for license_entry in licenses:
            license_url = str(license_entry.get("url", ""))
            if not re.fullmatch(
                r"(?:https://huggingface\.co/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/blob/[0-9a-f]{40}/\S+"
                r"|https://cdn\.jsdelivr\.net/gh/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}/\S+)",
                license_url,
            ):
                fail(f"model-set license URL is not immutable: {path.relative_to(ROOT)}")
        files = document.get("file")
        if not isinstance(files, list) or not files:
            fail(f"model-set has no files: {path.relative_to(ROOT)}")
        for entry in files:
            if "gated" in entry:
                fail(f"model-set file carries obsolete gated metadata: {path.relative_to(ROOT)}")
            if not re.fullmatch(r"[0-9a-f]{40}", str(entry.get("revision", ""))):
                fail(f"model file revision is not a full commit: {path.relative_to(ROOT)}")
            if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
                fail(f"model file SHA-256 is invalid: {path.relative_to(ROOT)}")
            if not isinstance(entry.get("size"), int) or entry["size"] <= 0:
                fail(f"model file size is invalid: {path.relative_to(ROOT)}")


def check_node_manager_git_guards() -> None:
    """Custom-node Git inspection must neutralize repository-controlled Git
    behavior (fsmonitor, hooks, optional locks)."""
    node_manager = read_text("scripts/manage-node-set.py")
    for git_guard in ("core.fsmonitor=false", "core.hooksPath=/dev/null", "GIT_OPTIONAL_LOCKS"):
        if git_guard not in node_manager:
            fail(f"custom-node Git inspection is missing: {git_guard}")


def check_cli_engine_and_remote_guard() -> None:
    """The CLI must preserve Dockerfile SHELL semantics under Podman and must
    require explicit acknowledgement before binding beyond localhost."""
    cli = read_cli_sources()
    if "export BUILDAH_FORMAT=docker" not in cli:
        fail("Podman builds must preserve Dockerfile SHELL semantics (export BUILDAH_FORMAT=docker)")
    if "validate_bind_address" not in cli or "COMFY_ALLOW_REMOTE:-false" not in cli:
        fail("the wrapper must require explicit acknowledgement (COMFY_ALLOW_REMOTE) for remote binding")
    if "running_service_container_ids" not in cli:
        fail("service state checks must use the shared engine-label helper")
    for unsupported_ps in ("ps --quiet comfy", "ps -q comfy"):
        if unsupported_ps in cli:
            fail(f"service state checks use provider-specific Compose syntax: {unsupported_ps}")
    if "podman-compose==1.6.0" not in read_text("requirements-dev.txt"):
        fail("the supported standalone podman-compose parser must be pinned for CI")
    if "bash tests/podman-compose.sh" not in read_text("tests/static.sh"):
        fail("the static gate must run standalone podman-compose compatibility checks")


def check_cli_node_workflows() -> None:
    """Custom-node snapshots must run through the helper container (never
    host Python), the node-set workflows must exist, and removed
    dependency-refresh controls must not resurface."""
    cli = read_cli_sources()
    env_example = read_text(".env.example")
    snapshot_function = cli.split("snapshot_node_dependencies()", 1)[1].split(
        "clear_node_dependencies()", 1
    )[0]
    if "scripts/snapshot-node-deps.py" in snapshot_function or " python " in snapshot_function:
        fail("custom-node snapshots must not invoke host Python")
    if "node-deps-snapshot" not in snapshot_function:
        fail("custom-node snapshots must run through the node-deps-snapshot helper container")
    if 'compose_tool()' not in cli or 'compose "$engine" --profile tools "$@"' not in cli:
        fail("containerized helper services must explicitly enable the tools Compose profile")
    tool_services = (
        "node-deps-snapshot",
        "node-set",
        "node-set-status",
        "model-set",
        "model-set-status",
        "template-inspector",
        "template-draft",
        "frontend-fetch",
        "frontend-build",
    )
    for line in cli.splitlines():
        if 'compose "$engine"' in line and any(
            f" {service}" in line for service in tool_services
        ):
            fail(f"helper service bypasses compose_tool: {line.strip()}")
    for node_contract in (
        "nodes install",
        "nodes sync",
        "nodes status",
        "models fetch",
        "models status",
        "--model-set",
        "templates list",
        "templates create-model-set",
    ):
        if node_contract not in cli:
            fail(f"custom-node CLI workflow is missing: {node_contract}")
    for removed_refresh_control in (
        "--refresh-node-deps",
        "LATENTCRATE_REFRESH_NODE_DEPS",
        "COMFY_REFRESH_NODE_DEPS_ON_UP",
    ):
        if removed_refresh_control in cli or removed_refresh_control in env_example:
            fail(f"removed dependency-refresh control remains: {removed_refresh_control}")


def check_sage_quarantine() -> None:
    """Sage installation runs as root inside the image build, so each Sage
    stage must drop the custom-node site from PYTHONPATH before installing
    and restore it only afterwards."""
    dockerfile = read_text("services/comfy/Dockerfile")
    if "PYTHONPATH=/opt/latentcrate-node-site:/opt/comfyui" not in dockerfile:
        fail("runtime stages must expose the custom-node site on PYTHONPATH")
    if "COPY --from=sage-builder /wheels" in dockerfile:
        fail("Sage wheel files must not be stored in final image layers")
    sage_wheel_mount = (
        "--mount=type=bind,from=sage-builder,source=/wheels,"
        "target=/tmp/sage-wheels,readonly"
    )
    if dockerfile.count(sage_wheel_mount) != 1:
        fail("the Sage target must install the wheel through a temporary read-only build mount")
    for sage_stage in ("FROM runtime AS runtime-sage",):
        section = dockerfile.split(sage_stage, 1)[1]
        section = re.split(r"^FROM ", section, maxsplit=1, flags=re.MULTILINE)[0]
        install_split = section.split("latentcrate-install-sage", 1)
        if len(install_split) != 2:
            fail(f"Sage stage does not run the shared Sage installer: {sage_stage}")
        if "ENV PYTHONPATH=/opt/comfyui" not in install_split[0]:
            fail(f"Sage installation does not isolate third-party node packages before installing: {sage_stage}")
        if "PYTHONPATH=/opt/latentcrate-node-site:/opt/comfyui" not in section.split("latentcrate-install-sage")[-1]:
            fail(f"Sage stage does not restore the custom-node site after installing: {sage_stage}")


def check_gpu_smoke_expectations() -> None:
    """GPU verification must assert every element of the requested build
    record so smoke runs validate what was actually asked for."""
    smoke = read_text("services/comfy/gpu-smoke.sh")
    for expected in (
        "LATENTCRATE_EXPECT_COMFYUI_REF",
        "LATENTCRATE_EXPECT_CUSTOM_NODE_CUDA_ARCH_LIST",
        "LATENTCRATE_EXPECT_FFMPEG_REF",
        "LATENTCRATE_EXPECT_FRONTEND_MODE",
        "LATENTCRATE_EXPECT_FRONTEND_COMMIT",
        "LATENTCRATE_EXPECT_FRONTEND_CONTENT_SHA256",
        "LATENTCRATE_EXPECT_FRONTEND_NODE_IMAGE",
        "LATENTCRATE_EXPECT_FRONTEND_PNPM_VERSION",
        "LATENTCRATE_EXPECT_NV_CODEC_HEADERS_REF",
        "LATENTCRATE_EXPECT_PYTORCH_DEVEL_IMAGE",
        "LATENTCRATE_EXPECT_PYTORCH_RUNTIME_IMAGE",
        "LATENTCRATE_EXPECT_SAGE",
        "LATENTCRATE_EXPECT_SAGEATTENTION_REF",
        "LATENTCRATE_EXPECT_SAGE_CUDA_ARCH_LIST",
        "LATENTCRATE_EXPECT_SVT_AV1_REF",
        "LATENTCRATE_EXPECT_TORCHCODEC_VERSION",
    ):
        if expected not in smoke:
            fail(f"GPU verification does not assert the requested build record: {expected}")
    if "nvenc_smoke_size=1920x1080" not in smoke:
        fail("GPU verification must use a cross-generation-safe NVENC frame size")


# --- Documentation -----------------------------------------------------------


def check_markdown_links() -> None:
    """Relative Markdown links must resolve to existing files."""
    for path in sorted(ROOT.rglob("*.md")):
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK_RE.finditer(text):
            raw_target = match.group("target").strip().strip("<>")
            target = raw_target.split("#", 1)[0]
            if not target or "://" in target or target.startswith(("mailto:", "#")):
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                fail(f"broken Markdown link in {path.relative_to(ROOT)}: {raw_target}")


CHECKS = (
    check_yaml_duplicate_keys,
    check_pins_single_source,
    check_pin_families,
    check_dockerfile_stage_graph,
    check_dockerfile_cache_decoupling,
    check_runtime_stage_arg_scope,
    check_frontend_runtime_contract,
    check_dist_frontend_overlay,
    check_frontend_tooling,
    check_frontend_helper_services,
    check_node_deps_build_isolation,
    check_node_deps_offline_install,
    check_node_wheel_builder,
    check_runtime_python_environment,
    check_media_stack,
    check_torchcodec_pinning,
    check_image_metadata,
    check_manager_defaults,
    check_entrypoint_privacy,
    check_entrypoint_sage_contract,
    check_comfy_service_hardened,
    check_env_example_defaults,
    check_excluded_default_features,
    check_node_deps_snapshot_service,
    check_node_set_services_hardened,
    check_model_set_services_hardened,
    check_template_services_hardened,
    check_model_set_manifests_pinned,
    check_node_manager_git_guards,
    check_cli_engine_and_remote_guard,
    check_cli_node_workflows,
    check_sage_quarantine,
    check_gpu_smoke_expectations,
    check_markdown_links,
)


def main() -> None:
    for check in CHECKS:
        check()
    print("LatentCrate repository checks passed.")


if __name__ == "__main__":
    sys.exit(main())
