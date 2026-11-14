"""Shared helpers for the package tests: a scratch checkout, a controlled
environment, and the fake engine on PATH."""

from __future__ import annotations

import contextlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
ENGINE_BIN = SOURCE_ROOT / "tests" / "fixtures" / "engine-bin"
COPIED = (
    "bin",
    "latentcrate",
    "config",
    "compose.yaml",
    "compose.gpu.yaml",
    "compose.podman.yaml",
    "compose.docker.yaml",
    "compose.frontend-dist.yaml",
    "pins.env",
    ".env.example",
)


def free_port() -> int:
    """A port nothing listens on, so `up` and `status` see no service."""
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Answering(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_: object) -> None:
        return


@contextlib.contextmanager
def serving(port: int):
    """A loopback HTTP server answering GET on the port, standing in for
    ComfyUI while a readiness poll runs."""
    server = HTTPServer(("127.0.0.1", port), _Answering)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class Scratch:
    """A temporary copy of the repository with its own home and storage
    directories, so no test touches the real checkout or $HOME."""

    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="latentcrate-test-"))
        self.root = self.tmp / "checkout"
        self.root.mkdir()
        for name in COPIED:
            source = SOURCE_ROOT / name
            if source.is_dir():
                shutil.copytree(source, self.root / name, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(source, self.root / name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.storage = self.tmp / "storage"
        for name in ("data", "models", "cache", "local-nodes"):
            (self.storage / name).mkdir(parents=True)
        self.port = str(free_port())

    def close(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def environment(self, **overrides: str) -> dict[str, str]:
        env = {
            "PATH": f"{ENGINE_BIN}:/usr/bin:/bin",
            "HOME": str(self.home),
            "PYTHONPATH": str(self.root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "CONTAINER_ENGINE": "docker",
            "COMFY_DATA_DIR": str(self.storage / "data"),
            "COMFY_MODELS_DIR": str(self.storage / "models"),
            "COMFY_CACHE_DIR": str(self.storage / "cache"),
            "COMFY_LOCAL_NODES_DIR": str(self.storage / "local-nodes"),
            "COMFY_PORT": self.port,
        }
        env.update(overrides)
        return {key: value for key, value in env.items() if value is not None}

    def run(self, *arguments: str, cwd: Path | None = None, **overrides: str) -> subprocess.CompletedProcess:
        """Run the package the way the shim does and capture both streams."""
        return subprocess.run(
            [sys.executable, "-m", "latentcrate", *arguments],
            cwd=cwd or self.root,
            env=self.environment(**overrides),
            capture_output=True,
            text=True,
            check=False,
        )

    def run_shim(self, *arguments: str, **overrides: str) -> subprocess.CompletedProcess:
        """Run the command through bin/latentcrate, as a user does."""
        return subprocess.run(
            ["bash", str(self.root / "bin" / "latentcrate"), *arguments],
            cwd=self.root,
            env=self.environment(**overrides),
            capture_output=True,
            text=True,
            check=False,
        )
