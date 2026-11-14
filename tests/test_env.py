"""Settings: precedence, storage paths, the bind address, the pins, the
token, and the subprocess environment."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from latentcrate import LatentCrateError, env
from tests.support import SOURCE_ROOT

STORAGE = ("COMFY_DATA_DIR", "COMFY_MODELS_DIR", "COMFY_CACHE_DIR", "COMFY_LOCAL_NODES_DIR")
PIN_KEYS = env.pin_keys(SOURCE_ROOT / "pins.env")


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="latentcrate-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # The example file is the defaults layer; a checkout always has it.
        (self.tmp / ".env.example").write_text((SOURCE_ROOT / ".env.example").read_text())

    def write_env(self, text: str) -> None:
        (self.tmp / ".env").write_text(text)
        (self.tmp / ".env").chmod(0o600)

    def test_shell_environment_wins_over_env_file_which_wins_over_defaults(self) -> None:
        self.write_env("COMFY_PORT=4210\nGPU_DEVICE=nvidia.com/gpu=1\n")
        settings = env.load(self.tmp, {"COMFY_PORT": "4300"})
        self.assertEqual(settings.port, "4300")
        self.assertEqual(settings.gpu_device, "nvidia.com/gpu=1")
        self.assertEqual(settings.service_url, "http://127.0.0.1:4300")
        self.assertEqual(settings.project_name, "latentcrate")

    def test_the_example_file_is_the_defaults_layer_and_a_checkout_without_it_is_refused(self) -> None:
        settings = env.load(self.tmp, {})
        self.assertEqual(settings.port, "4207")
        self.assertEqual(settings.sage, "off")
        self.assertEqual(settings.cuda_arch_list, "12.0")
        self.assertEqual(settings.host_model_gid, str(os.getgid()))
        self.assertEqual(settings.get("CONTAINER_ENGINE"), "")
        self.assertFalse(settings.allow_remote)
        self.assertNotIn("HF_TOKEN", settings.defaults)
        (self.tmp / ".env.example").unlink()
        with self.assertRaisesRegex(LatentCrateError, "no settings reference"):
            env.load(self.tmp, {})

    def test_every_default_reaches_subprocesses_with_its_effective_value(self) -> None:
        self.write_env("COMFY_PORT=4210\nCOMFY_BIND_ADDRESS=::1\n")
        subprocess_env = env.load(self.tmp, {}).subprocess_env()
        self.assertEqual(subprocess_env["COMFY_PORT"], "4210")
        self.assertEqual(subprocess_env["COMFY_BIND_ADDRESS"], "[::1]")
        self.assertEqual(subprocess_env["COMFYUI_EXTRA_ARGS"], "")
        for key, default in env.parse_env_file(SOURCE_ROOT / ".env.example").items():
            if key not in env.PATH_KEYS and key not in ("COMFY_PORT", "COMFY_BIND_ADDRESS"):
                self.assertEqual(subprocess_env[key], default, key)
        for key in env.PATH_KEYS:
            self.assertEqual(subprocess_env[key], str(self.tmp / env.parse_env_file(self.tmp / ".env.example")[key]))
        self.assertEqual(subprocess_env["LATENTCRATE_ROOT"], str(self.tmp))
        self.assertEqual(subprocess_env["HOST_UID"], str(os.getuid()))
        self.assertEqual(subprocess_env["HOST_GID"], str(os.getgid()))
        self.assertNotIn("HF_TOKEN", subprocess_env)
        for key in PIN_KEYS:
            self.assertNotIn(key, subprocess_env)

    def test_pins_cannot_be_overridden_from_the_shell_or_env_file(self) -> None:
        shutil.copy(SOURCE_ROOT / "pins.env", self.tmp)
        self.write_env("COMFYUI_REF=from-file\n")
        with self.assertRaisesRegex(LatentCrateError, "^.env sets COMFYUI_REF, which .env.example does not list"):
            env.load(self.tmp, {})
        (self.tmp / ".env").unlink()
        settings = env.load(self.tmp, {"PYTORCH_RUNTIME_TAG": "from-shell"})
        subprocess_env = settings.subprocess_env()
        for key in PIN_KEYS:
            self.assertNotIn(key, subprocess_env)
        # The keys are read from the checkout's pins.env at each call, and a
        # checkout without one still has a subprocess environment.
        (self.tmp / "pins.env").unlink()
        self.assertEqual(settings.subprocess_env()["PYTORCH_RUNTIME_TAG"], "from-shell")

    def test_container_identity_is_the_calling_user_and_never_a_setting(self) -> None:
        self.write_env("HOST_UID=1\nHOST_GID=1\n")
        with self.assertRaisesRegex(
            LatentCrateError, "^.env sets HOST_GID, HOST_UID, which .env.example does not list"
        ):
            env.load(self.tmp, {})
        (self.tmp / ".env").unlink()
        subprocess_env = env.load(self.tmp, {"HOST_UID": "2", "HOST_GID": "2"}).subprocess_env()
        self.assertEqual(subprocess_env["HOST_UID"], str(os.getuid()))
        self.assertEqual(subprocess_env["HOST_GID"], str(os.getgid()))

    def test_the_env_file_must_be_the_users_and_readable_by_the_user_alone(self) -> None:
        # It may hold the token; `init` creates it with mode 0600, a copy by
        # hand may not.
        self.write_env("COMFY_PORT=4210\n")
        path = self.tmp / ".env"
        for mode in (0o644, 0o640, 0o604):
            path.chmod(mode)
            with self.assertRaisesRegex(
                LatentCrateError, f"^{path} must be readable by your user only; run: chmod 600 {path}$"
            ):
                env.load(self.tmp, {})
        path.chmod(0o400)
        self.assertEqual(env.load(self.tmp, {}).port, "4210")
        other = os.getuid() + 1
        with mock.patch("latentcrate.env.os.getuid", return_value=other):
            with self.assertRaisesRegex(LatentCrateError, f"^{path} must be owned by your user \\(uid {other}\\)"):
                env.load(self.tmp, {})

    def test_symlinked_env_file_is_refused(self) -> None:
        (self.tmp / "real.env").write_text("COMFY_PORT=1\n")
        os.symlink(self.tmp / "real.env", self.tmp / ".env")
        with self.assertRaisesRegex(LatentCrateError, "refusing symbolic link"):
            env.load(self.tmp, {})

    def test_env_keys_missing_from_the_example_file_are_refused(self) -> None:
        # A commented-out key of the example (CONTAINER_ENGINE, HF_TOKEN) counts as listed.
        self.write_env("COMFY_PORT=4210\nCONTAINER_ENGINE=podman\nLLM_PORT=1\nCOMFY_BIND=0.0.0.0\n")
        refusal = (
            "^.env sets COMFY_BIND, LLM_PORT, which .env.example does not list; "
            "remove the line or set the variable in the shell instead$"
        )
        with self.assertRaisesRegex(LatentCrateError, refusal):
            env.load(self.tmp, {})
        self.write_env("COMFY_PORT=4210\nCONTAINER_ENGINE=podman\nHF_TOKEN=hf_fixture\n")
        settings = env.load(self.tmp, {})
        self.assertEqual(
            (settings.port, settings.get("CONTAINER_ENGINE"), settings.hf_token), ("4210", "podman", "hf_fixture")
        )

    def test_an_empty_path_setting_is_refused_not_defaulted(self) -> None:
        # Compose requires every setting; the wrapper reads these four
        # itself, so it is the one that names the variable and the remedy.
        for key in env.PATH_KEYS:
            with self.subTest(key=key):
                with self.assertRaisesRegex(LatentCrateError, f"^{key} must not be empty; omit the line to take"):
                    env.load(self.tmp, {key: ""})
        self.write_env("COMFY_MODELS_DIR=\n")
        with self.assertRaisesRegex(LatentCrateError, r"^COMFY_MODELS_DIR must not be empty; .*\(\./data/models\)$"):
            env.load(self.tmp, {})

    def test_relative_storage_paths_are_anchored_to_the_repository_root(self) -> None:
        self.write_env("COMFY_MODELS_DIR=./store/models\nCOMFY_DATA_DIR=store/data\nCOMFY_CACHE_DIR=/abs/cache\n")
        settings = env.load(self.tmp, {})
        self.assertEqual(settings.models_dir, self.tmp / "store" / "models")
        self.assertEqual(settings.data_dir, self.tmp / "store" / "data")
        self.assertEqual(settings.managed_nodes_dir, self.tmp / "store" / "data" / "custom_nodes")
        self.assertEqual(settings.cache_dir, Path("/abs/cache"))
        self.assertEqual(settings.local_nodes_dir, self.tmp / "local" / "custom_nodes")
        self.assertEqual(settings.build_dir, self.tmp / "build")
        self.assertEqual(settings.config_dir, self.tmp / "config")
        real = self.tmp / "real-models"
        real.mkdir()
        os.symlink(real, self.tmp / "linked-models")
        self.assertEqual(env.load(self.tmp, {"COMFY_MODELS_DIR": str(self.tmp / "linked-models")}).models_dir, real)

    def test_the_bind_address_is_loopback_or_acknowledged_and_never_localhost(self) -> None:
        # Compose publishes an IPv6 address in its bracket form only, so a
        # bare ::1 is normalised; load refuses nothing here, since doctor
        # reports the address and up raises through the property.
        for address in ("127.0.0.1", "[::1]"):
            self.assertEqual(env.load(self.tmp, {"COMFY_BIND_ADDRESS": address}).bind_address, address)
        settings = env.load(self.tmp, {"COMFY_BIND_ADDRESS": "::1"})
        self.assertEqual(settings.bind_address, "[::1]")
        self.assertEqual(settings.service_url, f"http://[::1]:{settings.port}")
        settings = env.load(self.tmp, {"COMFY_BIND_ADDRESS": "localhost"})
        refusal = f"^{env.BIND_NOT_ACCEPTED}$".replace("[", r"\[")
        self.assertRaisesRegex(LatentCrateError, refusal, getattr, settings, "bind_address")
        settings = env.load(self.tmp, {"COMFY_BIND_ADDRESS": "0.0.0.0"})
        refusal = (
            "^COMFY_BIND_ADDRESS=0.0.0.0 is not loopback; set COMFY_ALLOW_REMOTE=true only after adding "
            "authentication and TLS$"
        )
        self.assertRaisesRegex(LatentCrateError, refusal, getattr, settings, "service_url")
        settings = env.load(self.tmp, {"COMFY_BIND_ADDRESS": "0.0.0.0", "COMFY_ALLOW_REMOTE": "true"})
        self.assertEqual(settings.bind_address, "0.0.0.0")
        with self.assertRaisesRegex(LatentCrateError, "^COMFY_ALLOW_REMOTE must be one of 1/true/yes/on"):
            env.load(self.tmp, {"COMFY_ALLOW_REMOTE": "ture"})

    def test_a_port_is_used_as_written_so_it_has_no_leading_zero(self) -> None:
        # Compose publishes the value as written, so "04207" is refused,
        # not normalised; load leaves the check to the property.
        for value in ("0", "04207", "65536", "4207 ", "x"):
            with self.subTest(value=value):
                settings = env.load(self.tmp, {"COMFY_PORT": value})
                refusal = "^COMFY_PORT requires a port from 1 to 65535$"
                self.assertRaisesRegex(LatentCrateError, refusal, getattr, settings, "port")
        self.assertEqual(env.load(self.tmp, {"COMFY_PORT": "65535"}).port, "65535")

    def test_the_sage_mode_is_one_of_three_words_for_the_setting_and_the_flag(self) -> None:
        for word, mode in (("OFF", "off"), (" available ", "available"), ("global", "global")):
            self.assertEqual(env.load(self.tmp, {"LATENTCRATE_SAGE": word}).sage, mode)
        settings = env.load(self.tmp, {"LATENTCRATE_SAGE": "on"})
        refusal = "^LATENTCRATE_SAGE must be off, available, or global$"
        self.assertRaisesRegex(LatentCrateError, refusal, getattr, settings, "sage")
        self.assertEqual(env.SAGE_WORDS, "off, available, or global")

    def test_the_docker_model_group_defaults_to_the_calling_users_group(self) -> None:
        self.assertEqual(env.load(self.tmp, {}).subprocess_env()["HOST_MODEL_GID"], str(os.getgid()))
        with mock.patch("latentcrate.env.os.getgid", return_value=4321):
            self.assertEqual(env.load(self.tmp, {}).host_model_gid, "4321")
        self.write_env("HOST_MODEL_GID=1234\n")
        settings = env.load(self.tmp, {})
        self.assertEqual(settings.subprocess_env()["HOST_MODEL_GID"], "1234")
        self.assertEqual(settings.host_model_gid, "1234")
        settings = env.load(self.tmp, {"HOST_MODEL_GID": "1235"})
        self.assertEqual(settings.subprocess_env()["HOST_MODEL_GID"], "1235")
        self.assertEqual(settings.host_model_gid, "1235")

    def test_the_docker_model_group_is_a_numeric_id(self) -> None:
        self.assertEqual(env.load(self.tmp, {"HOST_MODEL_GID": "1234"}).host_model_gid, "1234")
        for value in ("", "models", "-1"):
            settings = env.load(self.tmp, {"HOST_MODEL_GID": value})
            refusal = "^HOST_MODEL_GID must be a numeric group id$"
            self.assertRaisesRegex(LatentCrateError, refusal, getattr, settings, "host_model_gid")

    def test_the_project_name_is_what_compose_keeps_of_it(self) -> None:
        # podman-compose drops every other character silently, which would
        # name another project's containers and networks.
        for name in ("Latent.Crate", "-x", "a b"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(LatentCrateError, "COMPOSE_PROJECT_NAME must be"):
                    env.load(self.tmp, {"COMPOSE_PROJECT_NAME": name})
        self.assertEqual(env.load(self.tmp, {"COMPOSE_PROJECT_NAME": ""}).project_name, "latentcrate")
        self.assertEqual(env.load(self.tmp, {"COMPOSE_PROJECT_NAME": "crate_2-b"}).project_name, "crate_2-b")

    def test_a_mounted_path_never_is_or_contains_the_checkout(self) -> None:
        # A storage directory that is or contains the checkout would mount
        # .env into the container.
        for value in (str(self.tmp), str(self.tmp.parent), "."):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    LatentCrateError, "^COMFY_LOCAL_NODES_DIR must not be or contain the LatentCrate checkout: "
                ):
                    env.load(self.tmp, {"COMFY_LOCAL_NODES_DIR": value})

    def test_two_mounted_paths_never_overlap(self) -> None:
        data = self.tmp / "data" / "comfy"
        with self.assertRaisesRegex(
            LatentCrateError,
            "^COMFY_DATA_DIR and COMFY_LOCAL_NODES_DIR must be separate directories, neither inside the other: "
            f"{data}, {data / 'custom_nodes'}$",
        ):
            env.load(self.tmp, {"COMFY_LOCAL_NODES_DIR": "data/comfy/custom_nodes"})
        with self.assertRaisesRegex(LatentCrateError, "^COMFY_MODELS_DIR and COMFY_CACHE_DIR must be separate"):
            env.load(self.tmp, {"COMFY_CACHE_DIR": "data/models", "COMFY_MODELS_DIR": "data/models"})
        with self.assertRaisesRegex(LatentCrateError, "^COMFY_DATA_DIR and COMFY_MODELS_DIR must be separate"):
            env.load(self.tmp, {"COMFY_DATA_DIR": "data"})

    def test_the_storage_map_names_every_path_the_rules_apply_to(self) -> None:
        settings = env.load(self.tmp, {})
        self.assertEqual(
            [
                (place.label, place.key, place.mounted, place.writable, place.created_by_init)
                for place in settings.storage
            ],
            [
                ("the LatentCrate checkout", "", False, False, False),
                ("the data directory", "COMFY_DATA_DIR", True, True, True),
                ("the managed custom-node directory", "", False, False, True),
                ("the models directory", "COMFY_MODELS_DIR", True, True, True),
                ("the cache directory", "COMFY_CACHE_DIR", True, True, True),
                ("the local custom-node directory", "COMFY_LOCAL_NODES_DIR", True, False, True),
                ("the private model-set directory", "", False, False, True),
            ],
        )
        by_label = {place.label: place for place in settings.storage}
        self.assertEqual(by_label["the LatentCrate checkout"].path, self.tmp.resolve())
        self.assertEqual(by_label["the private model-set directory"].path, self.tmp / "local" / "model-sets")
        self.assertEqual(
            by_label["the managed custom-node directory"].path, self.tmp / "data" / "comfy" / "custom_nodes"
        )
        self.assertEqual({place.key for place in settings.storage if place.key}, set(STORAGE))

    def test_the_tokens_are_captured_and_never_reach_a_subprocess_environment(self) -> None:
        # Read from the file or the shell into `hf_token` and
        # `civitai_token`, and out of everything exported; the shell wins,
        # and an empty shell value means "no token".
        self.write_env("HF_TOKEN=file-token\nCIVITAI_TOKEN=file-civitai\n")
        from_file = env.load(self.tmp, {})
        self.assertEqual((from_file.hf_token, from_file.civitai_token), ("file-token", "file-civitai"))
        from_shell = env.load(self.tmp, {"HF_TOKEN": "shell-token", "CIVITAI_TOKEN": "shell-civitai"})
        self.assertEqual((from_shell.hf_token, from_shell.civitai_token), ("shell-token", "shell-civitai"))
        for settings in (from_file, from_shell):
            for view in (settings.subprocess_env(), settings.values, settings.environ, settings.file_values):
                for key in env.TOKEN_KEYS:
                    self.assertNotIn(key, view)
            tokens = {settings.hf_token, settings.civitai_token}
            self.assertFalse(tokens & set(settings.subprocess_env().values()))
            self.assertFalse([token for token in tokens if token in repr(settings)])
        emptied = env.load(self.tmp, {"HF_TOKEN": "", "CIVITAI_TOKEN": ""})
        self.assertEqual((emptied.hf_token, emptied.civitai_token), (None, None))

    def test_a_token_with_a_character_no_header_takes_is_refused_by_its_key_alone(self) -> None:
        # `CIVITAI_TOKEN="$(cat key.txt)"` from a CRLF file keeps the CR.
        secret = "s3cret"
        for value in (f"{secret}\r", f"{secret}\n", f"{secret} x", f"{secret}\x01", f"{secret}é"):
            for key in env.TOKEN_KEYS:
                with self.subTest(key=key, value=value, source="shell"):
                    with self.assertRaises(LatentCrateError) as refusal:
                        env.load(self.tmp, {key: value})
                    self.assertEqual(
                        str(refusal.exception),
                        f"{key} holds a space, line break, or other character no token has; paste it again",
                    )
        for key in env.TOKEN_KEYS:
            for line in (f'{key}="{secret} x"', f"{key}={secret}\x01"):
                with self.subTest(line=line, source=".env"):
                    self.write_env(line + "\n")
                    with self.assertRaises(LatentCrateError) as refusal:
                        env.load(self.tmp, {})
                    self.assertNotIn(secret, str(refusal.exception))
                    self.assertTrue(str(refusal.exception).startswith(f"{key} holds"))

    def test_the_tokens_leave_this_process_so_no_subprocess_inherits_them(self) -> None:
        self.write_env("COMFY_PORT=4208\n")
        for key in env.TOKEN_KEYS:
            os.environ[key] = f"shell-{key}"
            self.addCleanup(os.environ.pop, key, None)
        settings = env.load(self.tmp)
        self.assertEqual((settings.hf_token, settings.civitai_token), ("shell-HF_TOKEN", "shell-CIVITAI_TOKEN"))
        for key in env.TOKEN_KEYS:
            self.assertNotIn(key, os.environ)

    def test_env_file_values_reach_subprocesses_and_quotes_are_stripped(self) -> None:
        self.write_env("COMFYUI_EXTRA_ARGS=\"--verbose DEBUG\"\nexport COMFY_TMPFS_SIZE='3g'\n# comment\n\n")
        settings = env.load(self.tmp, {"PATH": "/usr/bin"})
        subprocess_env = settings.subprocess_env(EXTRA="1")
        self.assertEqual(subprocess_env["COMFYUI_EXTRA_ARGS"], "--verbose DEBUG")
        self.assertEqual(subprocess_env["COMFY_TMPFS_SIZE"], "3g")
        self.assertEqual(subprocess_env["PATH"], "/usr/bin")
        self.assertEqual(subprocess_env["EXTRA"], "1")

    def test_values_that_expect_shell_expansion_are_refused(self) -> None:
        for value in ("$HOME/models", "~/models", "4208 # the port"):
            self.write_env(f"COMFY_MODELS_DIR={value}\n")
            with self.assertRaisesRegex(LatentCrateError, "used literally"):
                env.load(self.tmp, {})
        # Quoting keeps the value literal, and nothing expands a literal ~.
        self.write_env('COMFY_MODELS_DIR="~/models"\n')
        with self.assertRaisesRegex(LatentCrateError, "^COMFY_MODELS_DIR must not start with ~; write the full path$"):
            env.load(self.tmp, {})
        self.write_env("")
        with self.assertRaisesRegex(LatentCrateError, "^COMFY_CACHE_DIR must not start with ~; write the full path$"):
            env.load(self.tmp, {"COMFY_CACHE_DIR": "~user/cache"})
        self.write_env('COMFYUI_EXTRA_ARGS="--a # b"\n')
        self.assertEqual(env.load(self.tmp, {}).subprocess_env()["COMFYUI_EXTRA_ARGS"], "--a # b")

    def test_a_malformed_or_duplicate_line_is_reported_with_its_number(self) -> None:
        self.write_env("COMFY_PORT=1\nnot a setting\n")
        with self.assertRaisesRegex(LatentCrateError, r"\.env:2: expected KEY=VALUE"):
            env.load(self.tmp, {})
        self.write_env("COMFY_PORT=4210\nCOMFY_PORT=4211\n")
        with self.assertRaisesRegex(LatentCrateError, r"\.env:2: COMFY_PORT is set twice"):
            env.load(self.tmp, {})
        (self.tmp / ".env").write_bytes(b"\xef\xbb\xbfCOMFY_PORT=4212\n")
        self.assertEqual(env.load(self.tmp, {}).port, "4212")

    def test_an_env_file_that_is_not_utf8_text_is_refused_in_one_sentence(self) -> None:
        self.write_env("")
        (self.tmp / ".env").write_bytes(b"\xff")
        with self.assertRaisesRegex(LatentCrateError, f"^{self.tmp / '.env'} is not UTF-8 text$"):
            env.load(self.tmp, {})

    def test_an_env_entry_that_is_not_a_regular_file_is_refused(self) -> None:
        (self.tmp / ".env").mkdir()
        with self.assertRaisesRegex(LatentCrateError, f"^{self.tmp / '.env'} is not a file$"):
            env.load(self.tmp, {})

    def test_the_cuda_architecture_list_is_semicolon_separated_as_the_build_embeds_it(self) -> None:
        for value in ("12.0", "8.6+PTX", "8.6;8.9+PTX;12.0"):
            self.assertEqual(env.load(self.tmp, {"COMFY_CUDA_ARCH_LIST": value}).cuda_arch_list, value)
        for value in ("12.0, 8.6", "12.0 8.6", "12.0;", "sm_120", "12", ""):
            with self.subTest(value=value):
                settings = env.load(self.tmp, {"COMFY_CUDA_ARCH_LIST": value})
                refusal = f"^COMFY_CUDA_ARCH_LIST={value!r} is not a ;-separated list of CUDA architectures such as "
                with self.assertRaisesRegex(LatentCrateError, refusal):
                    str(settings.cuda_arch_list)

    def test_documented_keys_cover_the_commented_settings(self) -> None:
        documented = env.documented_keys(self.tmp / ".env.example")
        for key in (
            "HF_TOKEN",
            "CIVITAI_TOKEN",
            "CONTAINER_ENGINE",
            "LATENTCRATE_IMAGE",
            "MODEL_SET_EXTRA_WRITE_ROOTS",
            "COMFY_FRONTEND_MODE",
            "FRONTEND_GIT_URL",
            "FRONTEND_GIT_REF",
            "COMFY_FRONTEND_DIST_DIR",
            "COMFY_FRONTEND_SOURCE_DIR",
            *STORAGE,
            "COMFY_ALLOW_REMOTE",
        ):
            self.assertIn(key, documented)
        self.assertNotIn("HOST_UID", documented)
        self.assertNotIn("HOST_GID", documented)


class PinsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="latentcrate-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = self.tmp / "pins.env"

    def write(self, **overrides: str) -> Path:
        values = {
            "PYTORCH_DEVEL_TAG": "2.13.0-cuda13.0-cudnn9-devel",
            "PYTORCH_DEVEL_DIGEST": "a" * 64,
            "PYTORCH_RUNTIME_TAG": "2.13.0-cuda13.0-cudnn9-runtime",
            "PYTORCH_RUNTIME_DIGEST": "b" * 64,
            "TORCHCODEC_VERSION": "0.16.0+cu130",
            "CUDA_NPP_VERSION": "13.0.1.2",
            "COMFYUI_REF": "v0.34.0",
            "COMFYUI_COMMIT": "d" * 40,
            "COMFYUI_FRONTEND_REF": "v1.53.1",
            "COMFYUI_FRONTEND_COMMIT": "e" * 40,
            "FFMPEG_REF": "n9.0.1",
            "FFMPEG_COMMIT": "f" * 40,
            "FRONTEND_NODE_TAG": "26-bookworm-slim",
            "FRONTEND_NODE_DIGEST": "c" * 64,
            "FRONTEND_PNPM_VERSION": "11.24.0",
            "CUDA_MIN_DRIVER_MAJOR": "580",
        }
        values.update(overrides)
        self.path.write_text("# comment\n\n" + "".join(f"{key}={value}\n" for key, value in values.items()))
        return self.path

    def test_the_shipped_file_follows_the_grammar_and_names_one_cuda_family(self) -> None:
        values = env.load_pins(SOURCE_ROOT / "pins.env")
        self.assertEqual(tuple(values), PIN_KEYS)
        self.assertIn("PYTORCH_RUNTIME_DIGEST", values)
        self.assertEqual(env.cuda_family(values), "cu130")

    def test_a_new_pin_needs_no_code_when_it_follows_the_grammar(self) -> None:
        self.write(TOOL_VERSION="1.2.3", OTHER_TAG="v2", OTHER_DIGEST="0" * 64, SOME_REF="v3", SOME_COMMIT="1" * 40)
        values = env.load_pins(self.path)
        self.assertEqual(values["TOOL_VERSION"], "1.2.3")
        self.assertEqual(values["SOME_REF"], "v3")
        self.assertEqual(values["SOME_COMMIT"], "1" * 40)
        # File order is kept: `pins check` reports the pins in it.
        self.assertEqual(list(values)[:2], ["PYTORCH_DEVEL_TAG", "PYTORCH_DEVEL_DIGEST"])

    def test_quotes_and_whitespace_are_refused(self) -> None:
        for value in ('"v0.34.0"', "v0.34.0 ", "v0 34", "ghcr.io/x:v1"):
            self.write(COMFYUI_REF=value)
            with self.assertRaisesRegex(LatentCrateError, "unsafe value for COMFYUI_REF"):
                env.load_pins(self.path)

    def test_a_missing_duplicate_or_unknown_key_is_refused(self) -> None:
        self.path.write_text("FFMPEG_REF=n9.0.1\n")
        with self.assertRaisesRegex(
            LatentCrateError,
            "must define PYTORCH_DEVEL_TAG, PYTORCH_RUNTIME_TAG, TORCHCODEC_VERSION, CUDA_NPP_VERSION, "
            "COMFYUI_REF, COMFYUI_FRONTEND_COMMIT, CUDA_MIN_DRIVER_MAJOR",
        ):
            env.load_pins(self.path)
        self.write()
        self.path.write_text(self.path.read_text() + "FFMPEG_REF=n9.0.2\n")
        with self.assertRaisesRegex(LatentCrateError, "FFMPEG_REF is set twice"):
            env.load_pins(self.path)
        for key in ("TOOL_PYTHON_IMAGE", "TAG", "NODE_TAG_SHA", "TORCHCODEC_INDEX_URL"):
            self.write()
            self.path.write_text(self.path.read_text() + f"{key}=python\n")
            with self.assertRaisesRegex(
                LatentCrateError,
                f"unknown key\\(s\\) {key} \\(a pin is <NAME>_TAG with <NAME>_DIGEST, <NAME>_REF with <NAME>_COMMIT, "
                "<NAME>_VERSION, or CUDA_MIN_DRIVER_MAJOR\\)",
            ):
                env.load_pins(self.path)

    def test_the_driver_minimum_must_be_a_number(self) -> None:
        self.write(CUDA_MIN_DRIVER_MAJOR="latest")
        with self.assertRaisesRegex(LatentCrateError, "CUDA_MIN_DRIVER_MAJOR must be a driver major version"):
            env.load_pins(self.path)

    def test_a_digest_must_be_the_bare_sha256_hex(self) -> None:
        # The sha256: prefix is refused by the plain-value rule before this one.
        for value in ("a" * 63, "A" * 64, "sha256-" + "a" * 57):
            self.write(FRONTEND_NODE_DIGEST=value)
            with self.assertRaisesRegex(LatentCrateError, "FRONTEND_NODE_DIGEST must be the 64 hex characters"):
                env.load_pins(self.path)

    def test_a_tag_and_its_digest_come_as_a_pair(self) -> None:
        self.write(TOOL_DIGEST="0" * 64)
        with self.assertRaisesRegex(LatentCrateError, "TOOL_DIGEST has no TOOL_TAG beside it"):
            env.load_pins(self.path)
        self.write(TOOL_TAG="1.0")
        with self.assertRaisesRegex(LatentCrateError, "TOOL_TAG has no TOOL_DIGEST beside it"):
            env.load_pins(self.path)

    def test_a_commit_must_be_the_full_lowercase_hex(self) -> None:
        for value in ("d" * 39, "D" * 40, "v0.34.0"):
            self.write(COMFYUI_COMMIT=value)
            with self.assertRaisesRegex(LatentCrateError, "COMFYUI_COMMIT must be the 40 lowercase hex characters"):
                env.load_pins(self.path)

    def test_a_ref_and_its_commit_come_as_a_pair(self) -> None:
        self.write(TOOL_COMMIT="0" * 40)
        with self.assertRaisesRegex(LatentCrateError, "TOOL_COMMIT has no TOOL_REF beside it"):
            env.load_pins(self.path)
        self.write(TOOL_REF="v1.0")
        with self.assertRaisesRegex(LatentCrateError, "TOOL_REF has no TOOL_COMMIT beside it"):
            env.load_pins(self.path)

    def test_the_four_cuda_family_pins_change_together(self) -> None:
        rule = f"^{env.FAMILY_RULE}$"
        self.assertEqual(env.cuda_family(env.load_pins(self.write())), "cu130")
        for disagreement in (
            {"PYTORCH_DEVEL_TAG": "2.12.0-cuda13.0-cudnn9-devel"},
            {"TORCHCODEC_VERSION": "0.16.0+cu128"},
            {"CUDA_NPP_VERSION": "12.9.0.1"},
            {"PYTORCH_RUNTIME_TAG": "2.13.0-cpu-runtime", "PYTORCH_DEVEL_TAG": "2.13.0-cpu-devel"},
        ):
            with self.subTest(**disagreement):
                self.write(**disagreement)
                with self.assertRaisesRegex(LatentCrateError, rule):
                    env.load_pins(self.path)

    def test_a_malformed_line_names_its_number(self) -> None:
        self.write()
        self.path.write_text(self.path.read_text() + "not a pin\n")
        with self.assertRaisesRegex(LatentCrateError, r"pins\.env:19: expected KEY=VALUE"):
            env.load_pins(self.path)
        self.write()
        self.path.write_text(self.path.read_text().replace("COMFYUI_REF=", "export COMFYUI_REF=", 1))
        with self.assertRaisesRegex(LatentCrateError, r"pins\.env:9: expected KEY=VALUE"):
            env.load_pins(self.path)

    def test_the_key_scan_refuses_nothing_so_recovery_commands_run(self) -> None:
        # `subprocess_env` removes these keys whatever else the file holds;
        # a broken pins.env is reported by the launch commands, not at import.
        self.assertEqual(env.pin_keys(self.write())[:2], ("PYTORCH_DEVEL_TAG", "PYTORCH_DEVEL_DIGEST"))
        self.path.write_text('invalid pin edit\nFRONTEND_NODE_TAG="quoted"\n')
        self.assertEqual(env.pin_keys(self.path), ("FRONTEND_NODE_TAG",))
        with self.assertRaisesRegex(LatentCrateError, "expected KEY=VALUE"):
            env.load_pins(self.path)
        self.path.write_bytes(b"\xff")
        with self.assertRaisesRegex(LatentCrateError, f"^{self.path} is not UTF-8 text$"):
            env.pin_keys(self.path)
        self.path.unlink()
        self.assertEqual(env.pin_keys(self.path), ())
        with self.assertRaisesRegex(LatentCrateError, f"^pins file is missing: {self.path}$"):
            env.load_pins(self.path)


if __name__ == "__main__":
    unittest.main()
