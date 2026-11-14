"""Repository contracts that need no container engine.

Every test parses the artifact it inspects (Compose YAML, the Dockerfile,
the entrypoint, the settings reference, the manifests, the workflow, the
Markdown pages) and fails with the rule it enforces, pinning absence as
well as presence. Behaviour is proven elsewhere: the package by the other
unit tests, the rendered container arguments by tests/podman-compose.sh,
the image and the running stack on the GPU host.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import tomllib
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:
    raise ImportError(
        "the repository contracts need PyYAML: python3 -m pip install -r requirements-dev.txt "
        "(a silent skip would hollow out the gate, so the dependency is mandatory)"
    ) from None

from latentcrate import PROJECT_ROOT, cli, engine, env, models, nodes, templates, variants

DOCKERFILE = "services/comfy/Dockerfile"
ENTRYPOINT = "services/comfy/entrypoint.sh"
COMPOSE_FILES = (
    "compose.yaml",
    "compose.gpu.yaml",
    "compose.podman.yaml",
    "compose.docker.yaml",
    "compose.frontend-dist.yaml",
)
# What the one service may carry, so a second key cannot undo a hardening
# one (`privileged`, `pid`, `network_mode`, `cap_add`, `devices`).
SERVICE_KEYS = {
    "image",
    "build",
    "user",
    "init",
    "read_only",
    "restart",
    "stop_grace_period",
    "shm_size",
    "pids_limit",
    "cap_drop",
    "security_opt",
    "tmpfs",
    "ports",
    "volumes",
    "environment",
    "healthcheck",
}
# The bind targets and how each is mounted: the three writable storage
# directories and the read-only local node source.
MOUNTS = {
    "/data": ("COMFY_DATA_DIR", False),
    "/models": ("COMFY_MODELS_DIR", False),
    "/cache": ("COMFY_CACHE_DIR", False),
    "/local/custom_nodes": ("COMFY_LOCAL_NODES_DIR", True),
}
# The values the package derives on every Compose call (variants.Variant
# .compose_env and env.Settings.subprocess_env); everything else a Compose
# file references is a setting of .env.example or a pin of pins.env.
DERIVED = {
    "HOST_UID",
    "HOST_GID",
    "HOST_MODEL_GID",
    "LATENTCRATE_IMAGE",
    "COMFY_BUILD_TARGET",
    "COMFY_FRONTEND_MODE",
    "COMFY_FRONTEND_DIST_DIR",
    "FRONTEND_GIT_URL",
    "FRONTEND_GIT_REF",
    "FRONTEND_GIT_REQUESTED_REF",
}
# The variables a Compose file requires with `?` rather than `:?`: empty
# by design (no extra flags; dist mode leaves the Git reference empty).
EMPTY_ALLOWED = {"COMFYUI_EXTRA_ARGS", "FRONTEND_GIT_URL", "FRONTEND_GIT_REF", "FRONTEND_GIT_REQUESTED_REF"}
# The build arguments that are settings of .env.example, not pins.
SETTING_BUILD_ARGS = {"COMFY_CUDA_ARCH_LIST", "SAGE_BUILD_JOBS"}
# The variables the entrypoint reads that the image sets (ENV) or the dist
# overlay sets; every other one is passed by the comfy service.
IMAGE_VARIABLES = {"LATENTCRATE_FRONTEND_IMAGE_MODE", "LATENTCRATE_SAGE_ENABLED", "COMFY_FRONTEND_ROOT"}
# The variable the NVIDIA container runtime reads, not the entrypoint.
RUNTIME_VARIABLES = {"NVIDIA_DRIVER_CAPABILITIES"}
# The container environment: each variable from its required setting or
# derived value, so no value can be renamed or given a fallback unnoticed.
ENVIRONMENT = {
    "COMFY_ENABLE_MANAGER": "${COMFY_ENABLE_MANAGER:?exported by bin/latentcrate from .env}",
    "COMFY_FRONTEND_MODE": "${COMFY_FRONTEND_MODE:?set by bin/latentcrate}",
    "LATENTCRATE_SAGE_MODE": "${LATENTCRATE_SAGE:?exported by bin/latentcrate from .env}",
    "COMFY_DISABLE_API_NODES": "${COMFY_DISABLE_API_NODES:?exported by bin/latentcrate from .env}",
    "COMFY_DISABLE_METADATA": "${COMFY_DISABLE_METADATA:?exported by bin/latentcrate from .env}",
    "COMFYUI_EXTRA_ARGS": "${COMFYUI_EXTRA_ARGS?exported by bin/latentcrate from .env}",
    "NVIDIA_DRIVER_CAPABILITIES": "${NVIDIA_DRIVER_CAPABILITIES:?exported by bin/latentcrate from .env}",
    "UMASK": "${UMASK:?exported by bin/latentcrate from .env}",
}
# The privacy and containment defaults the settings reference ships.
PRIVACY_DEFAULTS = {
    "COMFY_DISABLE_API_NODES": "false",
    "COMFY_DISABLE_METADATA": "false",
    "COMFY_ALLOW_REMOTE": "false",
    "COMFY_PIDS_LIMIT": "2048",
    "COMFY_LOCAL_NODES_DIR": "./local/custom_nodes",
    "LATENTCRATE_SAGE": "off",
}
# The switches the entrypoint turns into ComfyUI flags.
ENTRYPOINT_SWITCHES = {
    "COMFY_ENABLE_MANAGER": "--enable-manager",
    "COMFY_DISABLE_API_NODES": "--disable-api-nodes",
    "COMFY_DISABLE_METADATA": "--disable-metadata",
}
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\((?P<target>[^)]+)\)")
# Every `${NAME...}` reference with its operator: `:-` (fallback), `:?` or
# `?` (required), or none.
COMPOSE_VARIABLE_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(:-|:\?|\?|)")
# Every base image is pulled by the pinned digest, never by the tag alone.
PINNED_FROM_RE = re.compile(r"^FROM \S+:\$\{([A-Z_]+)_TAG\}@sha256:\$\{([A-Z_]+)_DIGEST\} AS \S+$")
SHELL_VARIABLE_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)[:}]")
SHA_PINNED_ACTION_RE = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def read_text(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def code(relative: str) -> str:
    """The file without its comment lines, so a contract matches what runs,
    not what a comment says."""
    return "".join(line for line in read_text(relative).splitlines(keepends=True) if not line.lstrip().startswith("#"))


def load_yaml(relative: str) -> dict:
    document = yaml.safe_load(read_text(relative))
    if not isinstance(document, dict):
        raise AssertionError(f"{relative} did not parse to a YAML mapping")
    return document


def tracked(pattern: str) -> list[Path]:
    """The tracked and new files matching a pathspec: what ships, not a
    build artifact or a note under an ignored directory. Without a work
    tree (a `git archive` extract) the directory is scanned instead."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", pattern],
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        # Git's pathspec `*` crosses `/`, as fnmatch's does; the skipped
        # directories are the ignored ones.
        skipped = {".git", "build", "data", "local", "reports"}
        return sorted(
            path
            for path in PROJECT_ROOT.rglob("*")
            if path.is_file()
            and not skipped & set(path.relative_to(PROJECT_ROOT).parts)
            and fnmatch.fnmatchcase(path.relative_to(PROJECT_ROOT).as_posix(), pattern)
        )
    return sorted(path for path in (PROJECT_ROOT / p for p in listed.decode().split("\0") if p) if path.is_file())


def walk_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from walk_strings(key)
            yield from walk_strings(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from walk_strings(item)


def stage(dockerfile: str, name: str) -> str:
    """The text of one build stage, from its FROM line to the next."""
    match = re.search(rf"^FROM .+ AS {re.escape(name)}$", dockerfile, re.MULTILINE)
    if match is None:
        raise AssertionError(f"{DOCKERFILE} must define the stage {name}")
    rest = dockerfile[match.end() :]
    following = re.search(r"^FROM ", rest, re.MULTILINE)
    return rest if following is None else rest[: following.start()]


def heading_anchors(text: str) -> set[str]:
    """The anchors GitHub derives from a page's headings."""
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if not line.startswith("#"):
            continue
        title = line.lstrip("#").strip()
        base = "".join(character for character in title.lower() if character.isalnum() or character in " -_")
        base = base.replace(" ", "-")
        count = counts.get(base, 0)
        counts[base] = count + 1
        anchors.add(base if count == 0 else f"{base}-{count}")
    return anchors


class ProjectContractTests(unittest.TestCase):
    def comfy(self, relative: str = "compose.yaml") -> dict:
        services = load_yaml(relative).get("services") or {}
        self.assertEqual(set(services), {engine.SERVICE}, f"{relative} defines the {engine.SERVICE} service alone")
        return services[engine.SERVICE]

    def test_yaml_mappings_have_no_duplicate_keys(self) -> None:
        # A duplicate key in a literal YAML mapping silently overwrites the
        # earlier one, so a hardening line could vanish without a diff hunk
        # that says so.
        def walk(node: yaml.Node, relative: Path, path: tuple[str, ...] = ()) -> None:
            if isinstance(node, yaml.MappingNode):
                seen: set[str] = set()
                for key_node, value_node in node.value:
                    key = key_node.value if isinstance(key_node, yaml.ScalarNode) else "<complex>"
                    self.assertFalse(
                        key != "<<" and key in seen, f"duplicate YAML key in {relative}: {'.'.join((*path, key))}"
                    )
                    seen.add(key)
                    walk(value_node, relative, (*path, key))
            elif isinstance(node, yaml.SequenceNode):
                for index, item in enumerate(node.value):
                    walk(item, relative, (*path, str(index)))

        paths = [PROJECT_ROOT / relative for relative in COMPOSE_FILES]
        paths.extend(sorted((PROJECT_ROOT / ".github").rglob("*.yml")))
        for path in paths:
            node = yaml.compose(path.read_text(encoding="utf-8"))
            if node is not None:
                walk(node, path.relative_to(PROJECT_ROOT))

    def test_pins_env_is_the_only_place_a_pinned_value_lives(self) -> None:
        # The package validates the file (the key grammar, the digest
        # pairs, the CUDA family); this pins where each value may appear:
        # as a Dockerfile ARG without a default, passed by the comfy build
        # as a required argument, and nowhere with a fallback.
        pins = env.load_pins(PROJECT_ROOT / "pins.env")
        build_pins = set(pins) - {"CUDA_MIN_DRIVER_MAJOR"}
        dockerfile = code(DOCKERFILE)
        for match in re.finditer(r"^ARG ([A-Z0-9_]+)=", dockerfile, re.MULTILINE):
            self.fail(f"{DOCKERFILE} ARG {match.group(1)} has a default; build inputs come from pins.env and .env only")
        top_level = dockerfile.split("\nFROM ", 1)[0]
        declared = set(re.findall(r"^ARG ([A-Z0-9_]+)$", top_level, re.MULTILINE))
        derived_args = {key for key in DERIVED if key.startswith("FRONTEND_GIT_")}
        self.assertEqual(
            declared,
            build_pins | SETTING_BUILD_ARGS | derived_args,
            f"{DOCKERFILE} declares exactly the pins, the two build settings, and the frontend reference",
        )
        for line in dockerfile.splitlines():
            if line.startswith("FROM ") and "/" in line:
                match = PINNED_FROM_RE.match(line)
                self.assertIsNotNone(match, f"{DOCKERFILE} must pull a base image by pinned tag and digest: {line}")
                self.assertEqual(match.group(1), match.group(2), f"tag and digest of one image: {line}")
                self.assertIn(f"{match.group(1)}_TAG", pins, f"{line} names a pin pins.env lacks")
        args = self.comfy()["build"]["args"]
        self.assertEqual(set(args), declared, "compose.yaml passes every Dockerfile ARG and nothing else")
        for key in sorted(build_pins):
            self.assertEqual(
                args[key], f"${{{key}:?pin it in pins.env}}", f"pin {key} must be a required build argument"
            )
        for relative in (*COMPOSE_FILES, DOCKERFILE, "bin/latentcrate"):
            for match in re.finditer(r"\$\{([A-Z0-9_]+):-", read_text(relative)):
                self.assertNotIn(match.group(1), pins, f"pin {match.group(1)} has a fallback in {relative}")

    def test_the_comfy_service_is_contained_and_publishes_on_the_bind_address(self) -> None:
        compose = load_yaml("compose.yaml")
        self.assertEqual(set(compose), {"name", "services"}, "compose.yaml holds the project name and the service")
        comfy = self.comfy()
        self.assertEqual(set(comfy), SERVICE_KEYS, "the comfy service carries a key the contracts do not name")
        self.assertEqual(comfy["image"], "${LATENTCRATE_IMAGE:?set by bin/latentcrate}", "the image is derived")
        self.assertEqual(comfy["build"]["target"], "${COMFY_BUILD_TARGET:?set by bin/latentcrate}", "the target too")
        self.assertEqual(comfy["build"]["dockerfile"], DOCKERFILE)
        self.assertEqual(comfy["user"], "${HOST_UID:?set by bin/latentcrate}:${HOST_GID:?set by bin/latentcrate}")
        self.assertIs(comfy["init"], True)
        self.assertIs(comfy["read_only"], True, "the comfy service runs on a read-only root filesystem")
        self.assertEqual(comfy["cap_drop"], ["ALL"], "the comfy service drops every Linux capability")
        self.assertEqual(comfy["security_opt"], ["no-new-privileges:true"])
        self.assertEqual(comfy["pids_limit"], "${COMFY_PIDS_LIMIT:?exported by bin/latentcrate from .env}")
        self.assertEqual(
            comfy["tmpfs"],
            ["/tmp:rw,nosuid,nodev,mode=1777,size=${COMFY_TMPFS_SIZE:?exported by bin/latentcrate from .env}"],
            "/tmp is a bounded nosuid,nodev tmpfs and the only tmpfs",
        )
        self.assertEqual(
            comfy["ports"],
            [
                "${COMFY_BIND_ADDRESS:?exported by bin/latentcrate from .env}"
                f":${{COMFY_PORT:?exported by bin/latentcrate from .env}}:{engine.CONTAINER_PORT.split('/')[0]}"
            ],
            "the UI port is published on the bind address setting alone",
        )
        mounts = {}
        for mount in comfy["volumes"]:
            self.assertEqual(mount.get("type"), "bind", f"every mount is a long-form bind: {mount}")
            mounts[mount["target"]] = mount
        self.assertEqual(set(mounts), set(MOUNTS), "the comfy service binds a target the contracts do not name")
        for target, (setting, read_only) in MOUNTS.items():
            self.assertEqual(mounts[target]["source"], f"${{{setting}:?exported by bin/latentcrate from .env}}")
            self.assertEqual(bool(mounts[target].get("read_only")), read_only, f"{target} read-only: {read_only}")
        self.assertEqual(
            comfy["environment"], ENVIRONMENT, "the container environment is exactly the required settings"
        )
        self.assertEqual(comfy["healthcheck"]["test"][:2], ["CMD", "python"])

    def test_each_overlay_sets_exactly_its_one_concern(self) -> None:
        # The GPU device request stays out of compose.yaml so `templates`,
        # a CPU render, and --allow-no-gpu need no CDI device; each engine
        # overlay carries that engine's identity mapping alone; the dist
        # overlay binds the prebuilt frontend at the entrypoint's root.
        self.assertNotIn("devices", self.comfy(), "the device request belongs in compose.gpu.yaml")
        for relative in COMPOSE_FILES[1:]:
            self.assertEqual(set(load_yaml(relative)), {"services"}, f"{relative} changes the service alone")
        gpu = self.comfy("compose.gpu.yaml")
        self.assertEqual(set(gpu), {"devices"})
        self.assertEqual(gpu["devices"], ["${GPU_DEVICE:?exported by bin/latentcrate from .env}"])
        podman = self.comfy("compose.podman.yaml")
        self.assertEqual(set(podman), {"userns_mode", "group_add"})
        self.assertEqual(
            podman["userns_mode"],
            "keep-id:uid=${HOST_UID:?set by bin/latentcrate},gid=${HOST_GID:?set by bin/latentcrate}",
        )
        self.assertEqual(podman["group_add"], ["keep-groups"])
        docker = self.comfy("compose.docker.yaml")
        self.assertEqual(set(docker), {"group_add"})
        self.assertEqual(docker["group_add"], ["${HOST_MODEL_GID:?exported by bin/latentcrate}"])
        dist = self.comfy("compose.frontend-dist.yaml")
        self.assertEqual(set(dist), {"environment", "volumes"})
        root = re.search(r"^readonly DIST_FRONTEND_ROOT=(\S+)$", code(ENTRYPOINT), re.MULTILINE)
        self.assertIsNotNone(root, f"{ENTRYPOINT} names the dist frontend root")
        self.assertEqual(dist["environment"], {"COMFY_FRONTEND_MODE": "dist", "COMFY_FRONTEND_ROOT": root.group(1)})
        self.assertEqual(
            dist["volumes"],
            [
                {
                    "type": "bind",
                    "source": "${COMFY_FRONTEND_DIST_DIR:?set COMFY_FRONTEND_DIST_DIR}",
                    "target": root.group(1),
                    "read_only": True,
                }
            ],
            "the dist overlay binds the frontend read-only at the entrypoint's root and nothing else",
        )

    def test_every_compose_variable_is_a_required_setting_pin_or_derived_value(self) -> None:
        # The package exports every setting, every pin, and the derived
        # values on every Compose call, so each is required (`:?`, or `?`
        # where an empty value is a documented setting); a fallback would be
        # a second copy of a default.
        settings = env.documented_keys(PROJECT_ROOT / ".env.example")
        pins = set(env.load_pins(PROJECT_ROOT / "pins.env"))
        references: dict[str, set[str]] = {}
        for relative in COMPOSE_FILES:
            for key, operator in COMPOSE_VARIABLE_RE.findall(read_text(relative)):
                references.setdefault(key, set()).add(operator)
        with_fallback = {key for key, operators in references.items() if ":-" in operators or "" in operators}
        self.assertEqual(with_fallback, set(), "every Compose variable is required; none has a fallback")
        self.assertEqual({key for key, operators in references.items() if "?" in operators}, EMPTY_ALLOWED)
        self.assertLessEqual(set(references) - settings - pins, DERIVED, "a Compose variable nothing exports")
        self.assertLessEqual(pins - set(references), {"CUDA_MIN_DRIVER_MAJOR"}, "a pin no Compose file passes")
        for key in env.TOKEN_KEYS:
            self.assertIn(key, settings, f".env.example documents {key}")
            self.assertNotIn(key, references, f"{key} never reaches Compose")

    def test_the_tokens_never_reach_compose_or_the_image(self) -> None:
        self.assertEqual(env.TOKEN_KEYS, ("HF_TOKEN", "CIVITAI_TOKEN"))
        for relative in (*COMPOSE_FILES, DOCKERFILE, ENTRYPOINT):
            for key in env.TOKEN_KEYS:
                self.assertNotIn(key, read_text(relative), f"{key} must stay in the package, not in {relative}")

    def test_the_entrypoint_reads_what_compose_passes_and_wires_the_switches(self) -> None:
        # Every variable the entrypoint reads is passed by the comfy
        # service, set by the image, or set by the dist overlay; each
        # switch becomes its ComfyUI flag; the frontend root is explicit;
        # the container port is the one compose.yaml publishes and the
        # health check probes.
        entrypoint = code(ENTRYPOINT)
        read = set(SHELL_VARIABLE_RE.findall(entrypoint)) - IMAGE_VARIABLES
        passed = set(self.comfy()["environment"]) - RUNTIME_VARIABLES
        self.assertEqual(read, passed, "the entrypoint reads exactly the variables the comfy service passes")
        dockerfile = code(DOCKERFILE)
        for variable in IMAGE_VARIABLES - {"COMFY_FRONTEND_ROOT"}:
            self.assertTrue(re.search(rf"^ENV {variable}=", dockerfile, re.MULTILINE), f"the image sets {variable}")
        for variable, flag in ENTRYPOINT_SWITCHES.items():
            self.assertRegex(
                entrypoint,
                rf'if is_true "\$\{{{variable}:-(true|false)\}}"; then\n  args\+=\({re.escape(flag)}\)',
                f"{variable} switches {flag}",
            )
        self.assertTrue(
            re.search(
                r'^    global\)\n      if \[\[ "\$\{LATENTCRATE_SAGE_ENABLED:-0\}" != 1 \]\]; then\n'
                r"(?:.*\n){2}      fi\n"
                r"      args\+=\(--use-sage-attention\)\n",
                entrypoint,
                re.MULTILINE,
            ),
            "LATENTCRATE_SAGE_MODE=global applies Sage only on an image whose LATENTCRATE_SAGE_ENABLED marker is 1",
        )
        self.assertIn('"${LATENTCRATE_SAGE_MODE:-off}"', entrypoint, "the Sage mode defaults to off in the container")
        self.assertIn('--front-end-root "$frontend_root"', entrypoint, "ComfyUI serves the selected frontend root")
        port = engine.CONTAINER_PORT.split("/")[0]
        self.assertIn(f"--port {port}", entrypoint)
        self.assertIn(f"http://127.0.0.1:{port}/", code("services/comfy/healthcheck.py"))
        self.assertIn(templates.SCRIPT, dockerfile, f"the image installs the templates script at {templates.SCRIPT}")

    def test_the_settings_reference_describes_every_key_and_defaults_to_loopback(self) -> None:
        # The source documents itself: the first line names the file as the
        # reference, and every key line has a comment line of its own above
        # it; a key the package and the Compose files never read would
        # silently do nothing, so each must be named by one of them.
        lines = read_text(".env.example").splitlines()
        self.assertTrue(lines[0].startswith("# LatentCrate settings reference"), ".env.example says what it is")
        described = example = False
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                described = example = False
            elif env.DOCUMENTED_KEY_RE.match(line):
                # Commented-out keys that follow one another are one example
                # (a frontend mode with its variables) under one comment.
                self.assertTrue(
                    described or (example and line.startswith("#")),
                    f".env.example:{number}: a key needs a comment line above it",
                )
                described, example = False, line.startswith("#")
            elif line.startswith("#"):
                described = True
        defaults = env.parse_env_file(PROJECT_ROOT / ".env.example")
        self.assertIn(defaults["COMFY_BIND_ADDRESS"], env.LOOPBACK, "the UI binds loopback by default")
        for key, value in PRIVACY_DEFAULTS.items():
            self.assertEqual(defaults.get(key), value, f"privacy or containment default changed: {key}={value}")
        for key in env.TOKEN_KEYS:
            self.assertNotIn(key, defaults, f"{key} has no default value")
        sources = "".join(path.read_text(encoding="utf-8") for path in tracked("latentcrate/*.py"))
        sources += "".join(read_text(relative) for relative in COMPOSE_FILES)
        for key in sorted(env.documented_keys(PROJECT_ROOT / ".env.example")):
            self.assertIn(key, sources, f"{key} is documented but neither the package nor a Compose file reads it")

    def test_every_shipped_manifest_loads_through_the_package(self) -> None:
        # The loaders demand schema 1, one description line, and pinned
        # entries (the one rule); a manifest that fails them would fail every user.
        config = PROJECT_ROOT / "config"
        model_sets = models.catalog(config)
        self.assertEqual(set(model_sets), {path.stem for path in (config / "model-sets").glob("*.toml")})
        node_sets = nodes.catalog(config, nodes.load_allowed_hosts(config))
        self.assertEqual(set(node_sets), {path.stem for path in (config / "custom-nodes" / "sets").glob("*.toml")})
        self.assertTrue(model_sets and node_sets, "at least one manifest of each kind ships")

    def test_the_image_stage_graph_and_build_argument_scope(self) -> None:
        # Three targets with the documented inheritance; the shared runtime
        # layers reference no frontend or Sage input, so changing those pins
        # cannot invalidate the common cache; the runtime base redeclares
        # every argument its RUN lines and labels consume.
        dockerfile = code(DOCKERFILE)
        for edge in (
            "FROM comfy-runtime-base AS comfy-runtime",
            "FROM comfy-runtime AS runtime",
            "FROM runtime AS runtime-sage",
            "FROM runtime AS default",
        ):
            self.assertTrue(
                re.search(rf"^{re.escape(edge)}$", dockerfile, re.MULTILINE), f"{DOCKERFILE} stage graph misses: {edge}"
            )
        common = dockerfile[
            dockerfile.index(" AS comfy-python-base\n") : dockerfile.index(
                "FROM comfy-python-base AS comfy-runtime-base"
            )
        ]
        for unrelated in (
            "COMFYUI_FRONTEND_REF",
            "COMFYUI_FRONTEND_COMMIT",
            "SAGEATTENTION_REF",
            "SAGEATTENTION_COMMIT",
            "COMFY_CUDA_ARCH_LIST",
        ):
            self.assertNotIn(unrelated, common, f"the common runtime cache is coupled to {unrelated}")
        runtime_base = stage(dockerfile, "comfy-runtime-base")
        for name in (
            "COMFYUI_REF",
            "CUDA_NPP_VERSION",
            "FFMPEG_REF",
            "NV_CODEC_HEADERS_REF",
            "SVT_AV1_REF",
            "TORCHCODEC_VERSION",
        ):
            self.assertTrue(
                re.search(rf"^ARG {name}$", runtime_base, re.MULTILINE),
                f"comfy-runtime-base must redeclare build argument {name}",
            )
        self.assertIn('io.latentcrate.comfyui.ref="${COMFYUI_REF}"', runtime_base, "the image labels its ComfyUI pin")
        self.assertIn("io.latentcrate.frontend.", stage(dockerfile, "runtime"), "the image labels its frontend")
        self.assertIn("io.latentcrate.sageattention.", stage(dockerfile, "runtime-sage"), "the Sage image labels Sage")

    def test_every_tag_fetch_asserts_the_pinned_commit(self) -> None:
        # A tag can be moved, so every `_REF` in pins.env has its `_COMMIT`
        # beside it, and each fetch of a `_REF` is followed in the same RUN,
        # after the checkout, by a check of HEAD against that `_COMMIT`,
        # which the stage declares; the check's one alternative is its
        # failure branch, which exits, so no `|| true` can neutralise it. The frontend tag is the one `_REF` the
        # Dockerfile never fetches: release mode passes its `_COMMIT` as
        # FRONTEND_GIT_REF, which the frontend-git-builder checks.
        rule = "every git fetch of a _REF asserts the pinned _COMMIT in the same RUN"
        pins = env.load_pins(PROJECT_ROOT / "pins.env")
        refs = {key for key in pins if key.endswith("_REF")}
        commits = {key.removesuffix("_COMMIT") + "_REF" for key in pins if key.endswith("_COMMIT")}
        self.assertEqual(commits, refs, "pins.env pairs every _REF with its _COMMIT and nothing else")
        fetched: set[str] = set()
        for chunk in code(DOCKERFILE).split("\nFROM ")[1:]:
            for run in re.findall(r"^RUN (?:[^\n]*\\\n)*[^\n]*$", chunk, re.MULTILINE):
                for directory, name in re.findall(r'git -C (\S+) fetch --depth 1 origin "\$\{([A-Z0-9_]+)_REF\}"', run):
                    fetched.add(f"{name}_REF")
                    checkout = run.index(f"git -C {directory} checkout --detach FETCH_HEAD")
                    check = f'test "$(git -C {directory} rev-parse HEAD)" = "${{{name}_COMMIT}}"'
                    self.assertIn(check, run[checkout:], f"{rule}: {name}_REF in {DOCKERFILE}")
                    self.assertRegex(
                        run[checkout:],
                        re.escape(check) + rf" \\\n\s*\|\| \{{ printf '[^\n|]*not the pinned {name}_COMMIT[^\n|]*\n"
                        r"[^\n|]*>&2; exit 1; \}; \}(?: \\\n\s*&&|\Z)",
                        f"{rule}: {name}_REF names both pins and exits, and nothing else follows the check",
                    )
                    self.assertTrue(
                        re.search(rf"^ARG {name}_COMMIT$", chunk, re.MULTILINE),
                        f"the stage fetching {name}_REF declares {name}_COMMIT",
                    )
        self.assertEqual(fetched, refs - {"COMFYUI_FRONTEND_REF"}, f"{rule}: a _REF the Dockerfile never fetches")
        frontend = " ".join(stage(code(DOCKERFILE), "frontend-git-builder").split())
        self.assertRegex(
            frontend,
            re.escape('test "$(git rev-parse HEAD)" = "${FRONTEND_GIT_REF}"') + r" \\ && ",
            f"{rule}: the frontend commit, followed by && alone",
        )

    def test_third_party_node_packages_build_in_cuda_and_install_offline(self) -> None:
        # The copied requirement tree is rewritten and compiled in the pinned
        # CUDA development image; the runtime installs the resulting lock
        # with networking disabled, as an unprivileged user, and keeps the
        # compiler and the wheelhouse out of the final image.
        dockerfile = code(DOCKERFILE)
        builder = stage(dockerfile, "node-deps-builder")
        self.assertTrue(
            re.search(
                r"^FROM docker\.io/pytorch/pytorch:\$\{PYTORCH_DEVEL_TAG\}@sha256:\$\{PYTORCH_DEVEL_DIGEST\}"
                r" AS node-deps-builder$",
                dockerfile,
                re.MULTILINE,
            ),
            "third-party node wheels build in the pinned CUDA development image",
        )
        self.assertEqual(dockerfile.count("COPY build/custom-node-requirements"), 1, "the requirement tree enters once")
        self.assertIn(
            "COPY build/custom-node-requirements", builder, "the requirement tree enters node-deps-builder only"
        )
        rewrite = builder.find("rewrite-node-requirements.py /tmp/custom-node-requirements")
        build = builder.find("&& /usr/local/bin/latentcrate-build-node-deps")
        self.assertTrue(0 <= rewrite < build, "node-deps-builder rewrites the requirement tree before building wheels")
        self.assertIn("from=node-deps-builder", dockerfile, "the runtime consumes the CUDA-built packages")
        self.assertIn(
            "comfy-environment-constraints.txt", dockerfile, "resolution uses the ComfyUI environment constraints"
        )
        self.assertIn(
            "test ! -e /usr/local/cuda/bin/nvcc", dockerfile, "the runtime asserts the compiler stayed behind"
        )
        self.assertIn(
            "test ! -d /opt/latentcrate-node-wheelhouse", dockerfile, "the runtime asserts the wheelhouse stayed behind"
        )
        installer = code("services/comfy/install-node-deps.sh")
        for contract in (
            "install-requirements.txt",
            "--no-deps",
            "--no-index",
            "--only-binary=:all:",
            '--target "$target"',
        ):
            self.assertIn(contract, installer, f"the node package install is lock-driven and offline: {contract}")
        for forbidden in ("git+", "manifest.txt"):
            self.assertNotIn(
                forbidden, installer, "the final install consumes the built lock, not Git URLs or the manifest"
            )
        final_install = dockerfile.split("RUN --network=none", 1)
        self.assertEqual(len(final_install), 2, "the final node package install runs with networking disabled")
        self.assertIn("latentcrate-install-node-deps", final_install[1])
        self.assertIn("USER 65534:65534", dockerfile, "node packages install as an unprivileged user")
        wheel_builder = code("services/comfy/build-node-deps.sh")
        self.assertIn("python -m pip wheel", wheel_builder, "the wheel builder builds wheels")
        self.assertIn("symbolic links in the wheel directory", wheel_builder, "the wheel output rejects symbolic links")

    def test_the_runtime_python_environment_is_isolated_and_the_media_stack_pinned(self) -> None:
        # Runtime dependencies live in a virtual environment under reviewed
        # constraints, system protections stay intact, the user site stays
        # off the image PATH, one OpenCV provider is selected, FFmpeg and
        # TorchCodec come from the pins, and the image states what it is.
        dockerfile = code(DOCKERFILE)
        self.assertNotIn("PIP_NO_BUILD_ISOLATION", dockerfile, "build isolation stays on")
        self.assertNotIn("/data/home/.local/bin", dockerfile, "the durable user site stays off the image PATH")
        self.assertNotIn("--break-system-packages", dockerfile, "the runtime respects externally-managed Python")
        self.assertIn(
            "-m venv --system-site-packages /opt/latentcrate-venv", dockerfile, "runtime deps install into a venv"
        )
        self.assertIn("PATH=/opt/latentcrate-venv/bin:", dockerfile, "the venv is the default Python environment")
        self.assertIn(
            "runtime-constraints.txt >> /etc/latentcrate/core-constraints.txt",
            dockerfile,
            "constraints reach node installs",
        )
        replacements = tomllib.loads(read_text("config/python/custom-node-package-replacements.toml"))
        self.assertEqual(
            {entry["from"]: entry["to"] for entry in replacements["replacement"]},
            {
                "opencv-python": "opencv-contrib-python",
                "opencv-python-headless": "opencv-contrib-python",
                "opencv-contrib-python-headless": "opencv-contrib-python",
            },
            "the replacement policy selects one OpenCV cv2 provider",
        )
        for flag in ("--enable-libx265", "--enable-shared"):
            self.assertIn(flag, dockerfile, f"the FFmpeg build sets {flag}")
        self.assertIn(
            "LD_LIBRARY_PATH=/opt/latentcrate-media/lib:/opt/latentcrate-npp/lib:${LD_LIBRARY_PATH}",
            dockerfile,
            "the media and NPP library paths preserve the inherited NVIDIA paths",
        )
        self.assertIn("torchcodec==${TORCHCODEC_VERSION}", dockerfile, "TorchCodec installs from its pin")
        self.assertIn(
            "lib${library}.so.${CUDA_NPP_VERSION}", stage(dockerfile, "torchcodec-runtime-libs"), "NPP by pinned soname"
        )
        self.assertNotIn('org.opencontainers.image.licenses="MIT"', dockerfile, "the aggregate image is not MIT-only")
        for record in (
            "custom-node-requirements.sha256",
            "custom-node-packages.txt",
            "custom-node-build-environment.txt",
        ):
            self.assertNotIn(f"/usr/local/share/latentcrate/{record}", dockerfile, f"the image discloses {record}")
        for excluded in ("bootstrap-assets", "fonts-noto", "taesd"):
            self.assertNotIn(excluded, dockerfile.lower(), f"excluded default feature is present: {excluded}")
        self.assertIsNone(re.search(r"^\s*sox(?:\s|\\|$)", dockerfile, re.MULTILINE | re.IGNORECASE), "no SoX")

    def test_the_frontend_git_builder_hardens_git_as_the_package_does(self) -> None:
        # The stage carries a Bash copy of GIT_OPTIONS and git_environment:
        # every option and every GIT_* variable of the package reaches its
        # one RUN. HOME is the host's temporary directory there; the build
        # container keeps its own, with the global config already hidden.
        run = " ".join(stage(code(DOCKERFILE), "frontend-git-builder").split())
        for option in variants.GIT_OPTIONS[1::2]:
            self.assertIn(f"-c {option}", run, f"the frontend Git build lacks the package's Git option {option}")
        for key in variants.git_environment({}):
            if key.startswith("GIT_"):
                self.assertIn(f"{key}=", run, f"the frontend Git build lacks the package's Git variable {key}")

    def test_sage_installs_quarantined_from_the_node_site(self) -> None:
        # The Sage wheel installs as root through a temporary read-only
        # mount, with the custom-node site off PYTHONPATH until afterwards;
        # the GPU smoke test proves Sage absent from the default image.
        dockerfile = code(DOCKERFILE)
        self.assertIn(
            "PYTHONPATH=/opt/latentcrate-node-site:/opt/comfyui", dockerfile, "runtime stages expose the node site"
        )
        self.assertNotIn("COPY --from=sage-builder /wheels", dockerfile, "Sage wheels are not stored in final layers")
        mount = "--mount=type=bind,from=sage-builder,source=/wheels,target=/tmp/sage-wheels,readonly"
        self.assertEqual(dockerfile.count(mount), 1, "the Sage target installs through one temporary read-only mount")
        sage_stage = stage(dockerfile, "runtime-sage")
        before, after = sage_stage.split("latentcrate-install-sage", 1)
        self.assertIn("ENV PYTHONPATH=/opt/comfyui", before, "Sage installs with the node site off PYTHONPATH")
        self.assertIn(
            "PYTHONPATH=/opt/latentcrate-node-site:/opt/comfyui", after, "the Sage stage restores the node site"
        )
        self.assertIn('find_spec("sageattention") is not None', code("services/comfy/gpu-smoke.sh"))

    def test_ci_runs_the_strict_gate_on_the_python_floor(self) -> None:
        # One workflow: the strict gate on the floor bin/latentcrate
        # enforces, hadolint over the Dockerfile, SHA-pinned actions, read
        # permissions, a time limit; no image build and no CPU integration.
        workflows = sorted((PROJECT_ROOT / ".github" / "workflows").iterdir())
        self.assertEqual([path.name for path in workflows], ["static.yml"], "CI is the static gate alone")
        workflow = load_yaml(".github/workflows/static.yml")
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        floor = re.search(r"sys\.version_info >= \((\d+), (\d+)\)", read_text("bin/latentcrate"))
        self.assertIsNotNone(floor, "bin/latentcrate states the Python floor")
        python_floor = f"{floor.group(1)}.{floor.group(2)}"
        self.assertEqual(tomllib.loads(read_text("ruff.toml"))["target-version"], f"py{floor.group(1)}{floor.group(2)}")
        for name, job in workflow["jobs"].items():
            self.assertIn("timeout-minutes", job, f"job {name} needs timeout-minutes")
            steps = job["steps"]
            for step in steps:
                if "uses" in step:
                    self.assertRegex(
                        step["uses"], SHA_PINNED_ACTION_RE, f"job {name}: {step['uses']} is not SHA-pinned"
                    )
            versions = [step["with"]["python-version"] for step in steps if "setup-python" in step.get("uses", "")]
            self.assertEqual(versions, [python_floor], f"job {name} runs on the Python floor {python_floor}")
            runs = "\n".join(step.get("run", "") for step in steps)
            self.assertIn("python3 -m pip install -r requirements-dev.txt", runs)
            gate = [step for step in steps if step.get("run") == "bash tests/static.sh"]
            self.assertEqual(len(gate), 1, f"job {name} runs the gate once")
            self.assertEqual(
                (gate[0].get("env") or {}).get("LATENTCRATE_STATIC_STRICT"), "1", "CI runs the gate strict"
            )
            self.assertRegex(
                runs,
                rf"hadolint/hadolint:v[\d.]+@sha256:[0-9a-f]{{64}} .*\n.*< {DOCKERFILE}",
                "hadolint over the Dockerfile",
            )
            self.assertNotIn("docker build", runs, "CI builds no image")
        text = read_text(".github/workflows/static.yml")
        for step_uses in re.findall(r"uses: (\S+)(.*)", text):
            self.assertRegex(step_uses[1], r"# v\d", f"{step_uses[0]} names its version beside the SHA")

    def test_every_dependency_ecosystem_gets_dependabot_updates(self) -> None:
        # The Python gate dependencies and the workflow actions are the
        # ecosystems in the tree; the images are pinned in pins.env, which
        # `pins check` updates, so no `docker` entry.
        present = set()
        if read_text("requirements-dev.txt").strip():
            present.add("pip")
        if list((PROJECT_ROOT / ".github" / "workflows").glob("*.yml")):
            present.add("github-actions")
        manifests = [path for path in tracked("*/package.json") if "fixtures" not in path.parts]
        self.assertEqual(manifests, [], "an npm manifest would need a Dependabot entry")
        updates = load_yaml(".github/dependabot.yml")["updates"]
        listed = {entry["package-ecosystem"]: entry for entry in updates}
        self.assertEqual(set(listed), present, ".github/dependabot.yml covers exactly the ecosystems in the tree")
        for ecosystem, entry in listed.items():
            self.assertEqual(entry["directory"], "/", f"{ecosystem} updates read the repository root")
            self.assertTrue(entry.get("groups"), f"{ecosystem} updates arrive grouped")
        self.assertEqual(listed["pip"]["schedule"]["interval"], "monthly")
        self.assertEqual(listed["github-actions"]["schedule"]["interval"], "weekly")
        self.assertIn("docker", read_text(".github/dependabot.yml"), "the file says why there is no docker entry")

    # `help` is the syntax source; one verbatim copy sits on the page that
    # owns the command, so a flag change has one page to update (plan 3.7 b).
    def test_every_usage_line_sits_on_exactly_one_page(self) -> None:
        pages = {
            path.relative_to(PROJECT_ROOT).as_posix(): {
                line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            }
            for path in tracked("README.md") + tracked("docs/*.md")
        }
        for command in cli.COMMANDS:
            usage = command.usage.strip()
            holders = sorted(name for name, lines in pages.items() if usage in lines)
            self.assertEqual(
                len(holders), 1, f"the usage line `{usage}` sits on {len(holders)} pages, not one: {holders or 'none'}"
            )

    # The README table is the one map of the documentation: every page under
    # docs/ has a row, and no row points at a page that is not there.
    def test_the_readme_table_links_every_docs_page(self) -> None:
        readme = read_text("README.md")
        match = re.search(r"^## Choose what to do next\n(?P<body>.*?)(?=^## |\Z)", readme, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, "README.md has a section `## Choose what to do next`")
        linked = {
            re.sub(r"#.*$", "", link.group("target").strip())
            for link in MARKDOWN_LINK_RE.finditer(match.group("body"))
            if link.group("target").strip().startswith("docs/")
        }
        pages = {path.relative_to(PROJECT_ROOT).as_posix() for path in tracked("docs/*.md")}
        self.assertEqual(
            sorted(pages - linked), [], "the README table `Choose what to do next` links every page under docs/"
        )
        self.assertEqual(sorted(linked - pages), [], "every docs/ link in the README table names a tracked page")

    def test_the_documentation_links_and_anchors_resolve(self) -> None:
        pages = {path: path.read_text(encoding="utf-8") for path in tracked("*.md")}
        for path, text in pages.items():
            for match in MARKDOWN_LINK_RE.finditer(text):
                raw_target = match.group("target").strip().strip("<>")
                target, _, fragment = raw_target.partition("#")
                if "://" in target or target.startswith("mailto:"):
                    continue
                resolved = (path.parent / target).resolve() if target else path
                self.assertTrue(
                    resolved.exists(), f"broken Markdown link in {path.relative_to(PROJECT_ROOT)}: {raw_target}"
                )
                if fragment and resolved.suffix == ".md":
                    anchors = heading_anchors(pages.get(resolved, resolved.read_text(encoding="utf-8")))
                    self.assertIn(
                        fragment, anchors, f"broken heading anchor in {path.relative_to(PROJECT_ROOT)}: {raw_target}"
                    )


if __name__ == "__main__":
    unittest.main()
