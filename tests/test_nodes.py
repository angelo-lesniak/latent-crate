"""Custom-node sets: manifests, the per-node clones, the record and the
`up` check, status, export, and the lock, against bare repositories built
in the scratch directory. A `git` wrapper on PATH rewrites every
`https://github.com/<owner>/<repository>` argument to the matching bare
repository and logs the environment it was called with, so the manifests
keep real public URLs and no test reaches the network."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from latentcrate import LatentCrateError, engine, env, nodes
from tests.support import ENGINE_BIN, Scratch
from tests.test_engine import HOLDER

REAL_GIT = shutil.which("git", path="/usr/bin:/bin")
HARDENING = {
    "GIT_ASKPASS": "/bin/false",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_COUNT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "HOME": tempfile.gettempdir(),
}
WRAPPER = textwrap.dedent(
    """
    #!{python}
    import os, sys
    with open({log!r}, "a", encoding="utf-8") as log:
        log.write(" ".join(f"{{key}}={{os.environ.get(key, '<unset>')}}" for key in {keys!r}) + "\\n")
    # Only a fetch reaches the network; `remote add` must store the public URL.
    redirect = ["-c", "url.file://{bare}/.insteadOf=https://github.com/"] if "fetch" in sys.argv else []
    os.execv({git!r}, ["git", *redirect, *sys.argv[1:]])
    """
).lstrip()
IDENTITY = ("-c", "user.name=LatentCrate test", "-c", "user.email=test@invalid", "-c", "commit.gpgsign=false")


class NodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Scratch()
        self.addCleanup(self.scratch.close)
        self.bare = self.scratch.tmp / "bare"
        self.log = self.scratch.tmp / "git-env.log"
        wrapper_dir = self.scratch.tmp / "git-bin"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "git"
        wrapper.write_text(
            WRAPPER.format(
                python=sys.executable, log=str(self.log), keys=tuple(HARDENING), bare=self.bare, git=REAL_GIT
            )
        )
        wrapper.chmod(0o755)
        self.path = f"{wrapper_dir}:{ENGINE_BIN}:/usr/bin:/bin"
        self.managed = self.scratch.storage / "data" / "custom_nodes"
        self.managed.mkdir()
        self.hosts = nodes.load_allowed_hosts(self.scratch.root / "config")

    # --- fixtures ---------------------------------------------------------

    def git(self, *arguments: str, cwd: Path | None = None) -> str:
        """Real Git, outside the wrapper, with the caller's configuration kept out."""
        result = subprocess.run(
            [REAL_GIT, *IDENTITY, *arguments],
            cwd=cwd,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.scratch.home), **HARDENING},
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def commit(self, checkout: Path, text: str) -> str:
        (checkout / "node.py").write_text(text)
        self.git("add", "node.py", cwd=checkout)
        self.git("commit", "-q", "-m", text, cwd=checkout)
        return self.git("rev-parse", "HEAD", cwd=checkout)

    def repository(self, owner: str, name: str, commits: int = 2) -> list[str]:
        """A bare repository the wrapper serves for https://github.com/owner/name; its commits, oldest first."""
        work = self.scratch.tmp / "work" / owner / name
        work.mkdir(parents=True)
        self.git("init", "-q", str(work))
        history = [self.commit(work, f"{name} {number}") for number in range(1, commits + 1)]
        self.git("clone", "-q", "--bare", str(work), str(self.bare / owner / name))
        return history

    def checkout(self, path: Path, origin: str, *, dirty: bool = False) -> str:
        """A checkout with one commit and the given origin, as a user's installation holds it."""
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", str(path))
        commit = self.commit(path, path.name)
        self.git("remote", "add", "origin", origin, cwd=path)
        if dirty:
            (path / "local-change.txt").write_text("uncommitted\n")
        return commit

    def manifest(self, name: str, entries: list[tuple[str, str, str]], text: str = "") -> nodes.NodeSet:
        """A set manifest in the scratch catalog, read back through the module."""
        body = text or f'schema = 1\ndescription = "Fixture {name}"\n' + "".join(
            f'\n[[node]]\nname = "{node}"\nrepository = "{repository}"\ncommit = "{commit}"\n'
            for node, repository, commit in entries
        )
        path = self.scratch.root / "config" / "custom-nodes" / "sets" / f"{name}.toml"
        path.write_text(body)
        return nodes.read_manifest(path, self.hosts)

    def settings(self, **overrides: str) -> env.Settings:
        return env.load(self.scratch.root, self.scratch.environment(PATH=self.path, **overrides))

    def install(self, node_set: nodes.NodeSet, *, replace_clean: bool = False, **overrides: str) -> str:
        settings = self.settings(**overrides)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            nodes.install(settings, engine.Engine("docker", settings), node_set, replace_clean=replace_clean)
        return output.getvalue()

    def status(self, node_set: nodes.NodeSet) -> tuple[int, str]:
        settings = self.settings()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = nodes.status(settings, node_set, settings.subprocess_env())
        return result, output.getvalue()

    def head(self, name: str) -> str:
        return self.git("rev-parse", "HEAD", cwd=self.managed / name)

    def two_nodes(self) -> tuple[nodes.NodeSet, list[str], list[str]]:
        alpha, beta = self.repository("example", "Alpha"), self.repository("example", "beta")
        node_set = self.manifest(
            "pair",
            [
                ("Alpha", "https://github.com/example/Alpha", alpha[0]),
                ("beta", "https://github.com/example/beta", beta[0]),
            ],
        )
        return node_set, alpha, beta

    # --- manifests --------------------------------------------------------

    def test_shipped_manifest_loads_and_schema_is_enforced(self) -> None:
        shipped = self.scratch.root / "config" / "custom-nodes" / "sets" / "latent-nodepack.toml"
        node_set = nodes.read_manifest(shipped, self.hosts)
        self.assertEqual(node_set.name, "latent-nodepack")
        self.assertEqual(len(node_set.nodes), 10)
        self.assertIn("latent-nodepack", nodes.catalog(self.scratch.root / "config", self.hosts))
        self.assertEqual(nodes.public_repository("https://github.com/Owner/Repo.git"), "https://github.com/Owner/Repo")
        with self.assertRaisesRegex(LatentCrateError, "old: schema must be 1"):
            self.manifest("old", [], shipped.read_text().replace("schema = 1", "schema = 2", 1))
        with self.assertRaisesRegex(LatentCrateError, r"old: unknown key\(s\): version"):
            self.manifest("old", [], shipped.read_text().replace("schema = 1", "version = 1", 1))

    def test_manifest_refusals(self) -> None:
        good = ("Node", "https://github.com/example/node", "a" * 40)
        for label, entries, text, message in (
            ("unknown key", [], 'schema = 1\ndescription = "x"\nversion = 1\n', "unknown key"),
            ("no nodes", [], 'schema = 1\ndescription = "x"\n', r"at least one \[\[node\]\]"),
            ("hidden name", [(".Node", good[1], good[2])], "", "unsafe custom-node directory name"),
            ("disabled name", [("Node.disabled", good[1], good[2])], "", "unsafe custom-node directory name"),
            ("duplicate", [good, ("node", good[1], good[2])], "", "duplicate custom-node name: node"),
            ("short commit", [("Node", good[1], "abc123")], "", "full 40-character Git commit"),
            ("branch", [("Node", good[1], "main")], "", "full 40-character Git commit"),
            ("http", [("Node", "http://github.com/example/node", good[2])], "", "credential-free HTTPS"),
            ("credentials", [("Node", "https://user:pw@github.com/example/node", good[2])], "", "credential-free"),
            ("host", [("Node", "https://gitlab.com/example/node", good[2])], "", "allowed-git-hosts.txt"),
            ("query", [("Node", "https://github.com/example/node?x=1", good[2])], "", "credential-free HTTPS"),
            ("depth", [("Node", "https://github.com/example/node/tree", good[2])], "", "credential-free HTTPS"),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(LatentCrateError, message):
                self.manifest("bad", entries, text)
        with self.assertRaisesRegex(LatentCrateError, "unsafe custom-node set filename: all.toml"):
            self.manifest("all", [good])

    # --- install and sync -------------------------------------------------

    def test_install_clones_every_node_at_its_commit_and_writes_the_record(self) -> None:
        node_set, alpha, beta = self.two_nodes()
        output = self.install(node_set)
        self.assertEqual((self.head("Alpha"), self.head("beta")), (alpha[0], beta[0]))
        self.assertEqual((self.managed / nodes.RECORD_NAME).read_text(), "pair\n")
        self.assertEqual(
            output.splitlines(),
            [
                f"Installed Alpha at {alpha[0]}",
                f"Installed beta at {beta[0]}",
                f"Alpha: ready ({alpha[0]})",
                f"beta: ready ({beta[0]})",
                'Run "bash bin/latentcrate up" to build the node dependencies into the image.',
            ],
        )
        self.assertEqual(sorted(path.name for path in self.managed.iterdir()), [nodes.RECORD_NAME, "Alpha", "beta"])
        # A second install changes nothing.
        self.assertNotIn("Installed", self.install(node_set))
        self.assertEqual(self.status(node_set), (0, f"Alpha: ready ({alpha[0]})\nbeta: ready ({beta[0]})\n"))
        # Every Git call ran under the hardened environment.
        calls = self.log.read_text().splitlines()
        self.assertGreater(len(calls), 8)
        expected = " ".join(f"{key}={value}" for key, value in HARDENING.items())
        self.assertEqual(set(calls), {expected})

    def test_an_interrupted_install_is_completed_by_a_second_one(self) -> None:
        alpha = self.repository("example", "Alpha")
        gamma = self.repository("example", "gamma")
        node_set = self.manifest(
            "trio",
            [
                ("Alpha", "https://github.com/example/Alpha", alpha[0]),
                ("beta", "https://github.com/example/beta", "b" * 40),
                ("gamma", "https://github.com/example/gamma", gamma[0]),
            ],
        )
        with self.assertRaisesRegex(LatentCrateError, "git fetch failed in .*beta.latentcrate-clone.disabled"):
            self.install(node_set)
        self.assertEqual(sorted(path.name for path in self.managed.iterdir()), ["Alpha"])
        self.assertEqual(
            self.status(node_set)[1].splitlines()[1:], [f"beta: missing ({'b' * 40})", f"gamma: missing ({gamma[0]})"]
        )
        beta = self.repository("example", "beta")
        node_set = self.manifest(
            "trio",
            [
                ("Alpha", "https://github.com/example/Alpha", alpha[0]),
                ("beta", "https://github.com/example/beta", beta[0]),
                ("gamma", "https://github.com/example/gamma", gamma[0]),
            ],
        )
        output = self.install(node_set)
        self.assertNotIn("Installed Alpha", output)
        self.assertIn(f"Installed beta at {beta[0]}", output)
        self.assertEqual((self.head("beta"), self.head("gamma")), (beta[0], gamma[0]))
        self.assertEqual((self.managed / nodes.RECORD_NAME).read_text(), "trio\n")

    def test_leftover_staging_directories_are_removed_first(self) -> None:
        node_set, _, _ = self.two_nodes()
        leftover = self.managed / "Alpha.latentcrate-clone.disabled"
        (leftover / "partial").mkdir(parents=True)
        self.install(node_set)
        self.assertFalse(leftover.exists())
        self.assertEqual(self.status(node_set)[0], 0)

    def test_install_refuses_while_comfyui_runs_before_anything_changes(self) -> None:
        node_set, _, _ = self.two_nodes()
        leftover = self.managed / "Alpha.latentcrate-clone.disabled"
        leftover.mkdir()
        with self.assertRaisesRegex(LatentCrateError, "stop ComfyUI before installing or syncing a custom-node set"):
            self.install(node_set, FAKE_ENGINE_COMFY_RUNNING="true")
        self.assertEqual([path.name for path in self.managed.iterdir()], [leftover.name])

    def test_install_refuses_a_missing_or_linked_managed_directory(self) -> None:
        node_set, _, _ = self.two_nodes()
        self.managed.rmdir()
        with self.assertRaisesRegex(LatentCrateError, "does not exist: .*; run: bash bin/latentcrate init"):
            self.install(node_set)
        self.managed.symlink_to(self.scratch.tmp)
        with self.assertRaisesRegex(LatentCrateError, "must not be a symbolic link"):
            self.install(node_set)

    def test_a_dirty_checkout_refuses_install_and_sync(self) -> None:
        node_set, _, _ = self.two_nodes()
        self.install(node_set)
        change = self.managed / "Alpha" / "local-change.txt"
        change.write_text("uncommitted\n")
        for replace_clean in (False, True):
            with self.subTest(replace_clean=replace_clean):
                with self.assertRaisesRegex(
                    LatentCrateError, "refusing to replace Alpha: status is dirty; move or repair"
                ):
                    self.install(node_set, replace_clean=replace_clean)
                self.assertTrue(change.exists())

    def test_sync_replaces_a_clean_checkout_at_another_commit_and_refuses_a_dirty_one(self) -> None:
        node_set, alpha, beta = self.two_nodes()
        self.install(node_set)
        (self.managed / "beta" / "local-change.txt").write_text("uncommitted\n")
        updated = self.manifest(
            "pair-next",
            [
                ("Alpha", "https://github.com/example/Alpha", alpha[1]),
                ("beta", "https://github.com/example/beta", beta[1]),
            ],
        )
        with self.assertRaisesRegex(LatentCrateError, "refusing to replace Alpha: status is different-commit"):
            self.install(updated)
        with self.assertRaisesRegex(LatentCrateError, "refusing to replace beta: status is dirty"):
            self.install(updated, replace_clean=True)
        self.assertEqual((self.head("Alpha"), self.head("beta")), (alpha[1], beta[0]))
        self.assertEqual((self.managed / nodes.RECORD_NAME).read_text(), "pair\n")
        self.assertEqual(sorted(path.name for path in self.managed.iterdir()), [nodes.RECORD_NAME, "Alpha", "beta"])
        (self.managed / "beta" / "local-change.txt").unlink()
        self.assertIn(f"Installed beta at {beta[1]}", self.install(updated, replace_clean=True))
        self.assertEqual((self.managed / nodes.RECORD_NAME).read_text(), "pair-next\n")
        self.assertEqual(self.status(updated)[0], 0)

    def test_install_waits_for_the_lock_holder(self) -> None:
        node_set, _, _ = self.two_nodes()
        settings = self.settings()
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(self.scratch.root)],
            cwd=self.scratch.root,
            env=self.scratch.environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(holder.stdout.readline(), "held\n")
        docker = engine.Engine("docker", settings)
        self.assertEqual(docker.lock_file.read_text(), f"{holder.pid} build\n")
        self.assertEqual(self.status(node_set)[0], 1)
        release = threading.Timer(0.3, holder.stdin.close)
        with patch.object(engine, "warn") as warn:
            release.start()
            self.install(node_set)
        self.assertEqual(holder.wait(5), 0)
        holder.stdout.close()
        warn.assert_called_once_with(f"waiting for `build` (pid {holder.pid}) to finish")
        self.assertEqual(docker.lock_file.read_text(), f"{os.getpid()} nodes install\n")
        self.assertEqual(self.status(node_set)[0], 0)

    # --- status and the up check ------------------------------------------

    def test_status_reports_every_state(self) -> None:
        commits = {name: self.repository("example", name) for name in ("ready", "moved", "dirty", "other", "linked")}
        node_set = self.manifest(
            "states",
            [(name, f"https://github.com/example/{name}", history[0]) for name, history in commits.items()]
            + [
                ("missing", "https://github.com/example/missing", "c" * 40),
                ("plain", "https://github.com/example/plain", "d" * 40),
            ],
        )
        self.install(self.manifest("ready-only", [("ready", "https://github.com/example/ready", commits["ready"][0])]))
        # Origin case differs from the manifest: hosting services ignore it.
        self.git("remote", "set-url", "origin", "https://github.com/Example/READY.git", cwd=self.managed / "ready")
        self.install(self.manifest("moved-only", [("moved", "https://github.com/example/moved", commits["moved"][1])]))
        self.install(self.manifest("dirty-only", [("dirty", "https://github.com/example/dirty", commits["dirty"][0])]))
        (self.managed / "dirty" / "untracked.txt").write_text("x\n")
        self.install(self.manifest("other-only", [("other", "https://github.com/example/other", commits["other"][0])]))
        self.git("remote", "set-url", "origin", "https://github.com/example/elsewhere", cwd=self.managed / "other")
        (self.managed / "linked").symlink_to(self.scratch.tmp / "nowhere")
        (self.managed / "plain").mkdir()
        result, output = self.status(node_set)
        self.assertEqual(result, 1)
        self.assertEqual(
            output.splitlines(),
            [
                f"ready: ready ({commits['ready'][0]})",
                f"moved: different-commit ({commits['moved'][0]})",
                f"dirty: dirty ({commits['dirty'][0]})",
                f"other: different-repository ({commits['other'][0]})",
                f"linked: conflict ({commits['linked'][0]})",
                f"missing: missing ({'c' * 40})",
                f"plain: conflict ({'d' * 40})",
            ],
        )
        self.git("config", "filter.evil.clean", "false", cwd=self.managed / "ready")
        with self.assertRaisesRegex(LatentCrateError, "refusing execution-capable local Git configuration in .*ready"):
            self.status(node_set)

    def test_check_prints_the_set_without_touching_the_checkouts(self) -> None:
        node_set, alpha, beta = self.two_nodes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            nodes.check(node_set)
        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "Fixture pair",
                f"Alpha {alpha[0]} https://github.com/example/Alpha",
                f"beta {beta[0]} https://github.com/example/beta",
            ],
        )
        self.assertEqual(list(self.managed.iterdir()), [])
        self.assertFalse(self.log.exists())

    def test_require_selected_set(self) -> None:
        node_set, _, _ = self.two_nodes()
        record = self.managed / nodes.RECORD_NAME
        settings = self.settings()
        self.assertEqual(nodes.selected_set(self.managed), "")
        nodes.require_selected_set(settings)
        self.install(node_set)
        self.assertEqual(nodes.selected_set(self.managed), "pair")
        nodes.require_selected_set(settings)
        shutil.rmtree(self.managed / "beta")
        self.assertEqual(nodes.missing_nodes(self.managed, node_set), ["beta"])
        with self.assertRaisesRegex(
            LatentCrateError, "custom-node set pair is missing beta; run: bash bin/latentcrate nodes install pair"
        ):
            nodes.require_selected_set(settings)
        record.write_text("gone\n")
        with self.assertRaisesRegex(LatentCrateError, f"{record} names an unknown custom-node set gone"):
            nodes.require_selected_set(settings)
        record.write_text("../x\n")
        with self.assertRaisesRegex(LatentCrateError, f"{record} does not name a custom-node set"):
            nodes.require_selected_set(settings)

    # --- export -----------------------------------------------------------

    def test_export_prints_a_manifest_for_the_clean_public_checkouts(self) -> None:
        custom = self.scratch.tmp / "custom_nodes"
        for name in (".disabled", ".HiddenNode", "__pycache__", "Ignored.disabled", "LocalNode"):
            (custom / name).mkdir(parents=True)
        (custom / "LocalNode" / "__init__.py").write_text("local only\n")
        clean = self.checkout(custom / "CleanNode", "git@github.com:example/CleanNode.git")
        ssh = self.checkout(custom / "SshNode", "ssh://git@github.com/example/SshNode")
        self.checkout(custom / "DirtyNode", "https://github.com/example/DirtyNode.git", dirty=True)
        self.checkout(custom / "ForeignNode", "https://gitlab.com/example/ForeignNode")
        self.checkout(custom / "HookedNode", "https://github.com/example/HookedNode")
        self.git("config", "core.hooksPath", "x", cwd=custom / "HookedNode")
        self.checkout(custom / "cleannode", "https://github.com/example/cleannode")
        (custom / "LinkNode").symlink_to(custom / "CleanNode")
        self.git("init", "-q", str(custom / "EmptyNode"))
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            text = nodes.export(custom, self.scratch.environment(), self.hosts)
        self.assertEqual(
            text,
            "schema = 1\n"
            'description = "Third-party nodes exported from an existing ComfyUI installation"\n\n'
            f'[[node]]\nname = "CleanNode"\nrepository = "https://github.com/example/CleanNode"\ncommit = "{clean}"\n\n'
            f'[[node]]\nname = "SshNode"\nrepository = "https://github.com/example/SshNode"\ncommit = "{ssh}"\n',
        )
        exported = self.manifest("exported", [], text)
        self.assertEqual([node.name for node in exported.nodes], ["CleanNode", "SshNode"])
        dirty = self.git("rev-parse", "HEAD", cwd=custom / "DirtyNode")
        reported = [
            ("DirtyNode", f"working tree is dirty at {dirty}; uncommitted changes cannot be pinned"),
            ("EmptyNode", "Git origin is missing or unreadable"),
            ("ForeignNode", "origin is not a public, credential-free repository on an allowed host"),
            ("HookedNode", "checkout has execution-capable or credential-related local Git configuration"),
            ("LinkNode", "top-level symbolic links cannot be exported"),
            ("LocalNode", "not a regular Git checkout; copy it through COMFY_LOCAL_NODES_DIR instead"),
            ("cleannode", "another node has the same name when case is ignored"),
        ]
        self.assertEqual(
            stderr.getvalue().splitlines(),
            [f"LatentCrate: Needs manual handling: {name} - {reason}" for name, reason in reported]
            + [
                "LatentCrate: Exported 2 node(s); 7 need manual handling. "
                "Review every commit before sharing or installing the set."
            ],
        )
        with self.assertRaisesRegex(LatentCrateError, "no clean public node checkouts could be exported"):
            with contextlib.redirect_stderr(io.StringIO()):
                nodes.export(custom / "LocalNode", self.scratch.environment(), self.hosts)
        with self.assertRaisesRegex(LatentCrateError, "custom-node directory does not exist"):
            nodes.export(custom / "nowhere", self.scratch.environment(), self.hosts)


if __name__ == "__main__":
    unittest.main()
