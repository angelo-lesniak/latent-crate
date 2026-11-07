"""Command-line dispatch: the command table, option parsing, and usage.

`COMMANDS` is the one description of the interface: each entry is a usage
line, the flags it accepts, and its handler. `USAGE` and every refusal of
a misplaced flag are generated from it. Usage and error text are part of
the documented interface, so a change here changes the page."""

from __future__ import annotations

import contextlib
import io
import re
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property, partial
from pathlib import Path
from typing import Any

from . import PROJECT_ROOT, LatentCrateError, checkpins, doctor, env, files, warn
from .names import SET_NAME_RE

POSITIVE_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")

# The file `init` writes: comments only, so every value stays in .env.example.
ENV_HEADER = (
    "# LatentCrate local settings. Every setting and its default is in .env.example;\n"
    "# a line here overrides it.\n"
    "# Keep this file private: it may hold HF_TOKEN.\n"
)


@dataclass
class Context:
    """What a handler may need, each read once on first use, so a command
    that needs no settings or no pins never reads them."""

    root: Path

    @cached_property
    def settings(self) -> env.Settings:
        return env.load(self.root)

    @cached_property
    def pinned(self) -> dict[str, str]:
        return env.load_pins(self.root / "pins.env")


Options = dict[str, Any]
Handler = Callable[[Context, Options, list[str]], int | None]


@dataclass(frozen=True)
class Flag:
    """One option. `what` names the value it carries (empty for a switch),
    `valid` accepts that value, and `repeat` collects every occurrence into
    a list instead of refusing the second."""

    what: str = ""
    valid: Callable[[str], object] = lambda raw: True
    repeat: bool = False


@dataclass(frozen=True)
class Command:
    """One usage line. `name` is the command and, after a space, either its
    subcommand (`models fetch`) or the flag that selects a mode of it
    (`up --frontend-git`); `synopsis` is the rest of the usage line;
    `arguments` bounds the positional arguments (None: unbounded);
    `exclusive` lists the flag pairs that may not be given together."""

    name: str
    synopsis: str
    summary: str
    flags: dict[str, Flag]
    handler: Handler
    arguments: tuple[int, int | None] = (0, 0)
    exclusive: tuple[tuple[str, str], ...] = ()

    @property
    def family(self) -> str:
        return self.name.split()[0]

    @property
    def selector(self) -> str:
        words = self.name.split()
        return words[1] if len(words) > 1 and words[1].startswith("--") else ""

    @property
    def usage(self) -> str:
        return f"bin/latentcrate {self.name} {self.synopsis}".rstrip()


SWITCH = Flag()
SAGE = Flag(f"a mode: {env.SAGE_WORDS}", lambda raw: raw.strip().lower() in env.SAGE_MODES)
POSITIVE = Flag("a positive integer", POSITIVE_INTEGER_RE.fullmatch)
# One flag per set rather than a comma list, so a set name is never split.
MODEL_SET = Flag("a model set name", SET_NAME_RE.fullmatch, repeat=True)
NAME = Flag("a model set name", SET_NAME_RE.fullmatch)
DIRECTORY = Flag("a directory", bool)


# -- Parsing -------------------------------------------------------------------


def usage_error(name: str) -> LatentCrateError:
    return LatentCrateError(f"usage: {BY_NAME[name].usage}")


def names_for(flag: str, commands: list[Command]) -> str:
    """`a, b, or c` for the commands that accept `flag`: a family whose base
    command accepts it, or whose mode it selects, is named once, in the
    order of the usage text."""
    names: list[str] = []
    for command in commands:
        base = BY_NAME.get(command.family)
        name = command.family if command.selector == flag or base in commands else command.name
        if name not in names:
            names.append(name)
    if len(names) > 2:
        return ", ".join(names[:-1]) + ", or " + names[-1]
    return " or ".join(names)


def refusal(command: Command, flag: str) -> str:
    """Why `command` does not take `flag`: another command does, another
    mode of the same command does, or nothing does."""
    accepting = [entry for entry in COMMANDS if flag in entry.flags]
    related = [entry for entry in accepting if entry.family == command.family]
    if not accepting:
        return f"unknown option for {command.name}: {flag}"
    if not related:
        return f"{flag} is only valid with {names_for(flag, accepting)}"
    if command.selector and BY_NAME.get(command.family) in related:
        return f"{flag} is not valid with {command.selector}"
    return f"{flag} applies to {names_for(flag, related)}"


def resolve(argv: list[str]) -> tuple[Command, list[str]]:
    """The table entry `argv` names and the arguments left for it: the
    subcommand when the second word is one, else the mode whose selector
    appears, else the command itself."""
    word = argv[0]
    family = [entry for entry in COMMANDS if entry.family == word]
    if not family:
        sys.stderr.write(USAGE)
        raise LatentCrateError(f"unknown command: {word}")
    subcommand = BY_NAME.get(" ".join(argv[:2]))
    if subcommand is not None and not subcommand.selector:
        return subcommand, argv[2:]
    base = BY_NAME.get(word)
    if base is None:
        raise LatentCrateError(
            f"usage: bin/latentcrate {word} " + " | ".join(entry.name.split()[1] for entry in family)
        )
    arguments = argv[1:]
    for mode in family:
        if mode.selector and mode.selector in arguments:
            return mode, arguments
    return base, arguments


def parse(command: Command, arguments: list[str]) -> tuple[Options, list[str]]:
    """The flags of `command` with their values (True for a switch, a list
    for a repeatable flag) and its positional arguments. A repeated flag is
    refused rather than resolved to one occurrence, so a typo in either
    stays visible."""
    options: Options = {}
    names: list[str] = []
    least, most = command.arguments
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if not argument.startswith("--"):
            if most == 0:
                raise LatentCrateError(f"unexpected argument for {command.name}: {argument}")
            names.append(argument)
            index += 1
            continue
        flag = command.flags.get(argument)
        if flag is None:
            raise LatentCrateError(refusal(command, argument))
        if argument in options and not flag.repeat:
            raise LatentCrateError(f"{argument} may be given once with {command.name}")
        if not flag.what:
            options[argument] = True
            index += 1
            continue
        if index + 1 >= len(arguments) or not flag.valid(arguments[index + 1]):
            raise LatentCrateError(f"{argument} requires {flag.what}")
        if flag.repeat:
            options.setdefault(argument, []).append(arguments[index + 1])
        else:
            options[argument] = arguments[index + 1]
        index += 2
    if len(names) < least or (most is not None and len(names) > most):
        raise usage_error(command.name)
    for first, second in command.exclusive:
        if first in options and second in options:
            raise LatentCrateError(f"{first} and {second} may not be combined with {command.name}")
    return options, names


# -- Handlers ------------------------------------------------------------------


def cmd_init(context: Context, _options: Options, _names: list[str]) -> None:
    settings = context.settings
    created = [place for place in settings.storage if place.created_by_init]
    for place in created:
        if place.path.exists() and not place.path.is_dir():
            raise LatentCrateError(f"{place.key or place.label} is not a directory: {place.path}")
    for place in created:
        place.path.mkdir(parents=True, exist_ok=True)
    env_file = settings.root / ".env"
    if env_file.is_file():
        print(f"Keeping existing {env_file}")
    else:
        files.write_atomic(env_file, ENV_HEADER, mode=0o600)
        print(f"Created {env_file}")
    print("LatentCrate host directories are ready. Review .env before building.")


def cmd_doctor(context: Context, options: Options, _names: list[str]) -> int:
    sage = options["--sage"].strip().lower() if "--sage" in options else context.settings.sage
    return doctor.run(
        context.settings,
        min_driver_major=int(context.pinned["CUDA_MIN_DRIVER_MAJOR"]),
        allow_no_gpu="--allow-no-gpu" in options,
        sage=sage,
    )


def cmd_pins(context: Context, _options: Options, _names: list[str]) -> None:
    # Through the validating loader, so a bad edit is a refusal.
    sys.stdout.write("".join(f"{key}={value}\n" for key, value in context.pinned.items()))


def cmd_pins_check(context: Context, _options: Options, names: list[str]) -> None:
    try:
        sys.stdout.write(checkpins.check(names[0] if names else "all", context.pinned))
    except checkpins.LookupsFailed as failure:
        sys.stdout.write(failure.report)
        raise


def not_ported(name: str, _context: Context, _options: Options, _names: list[str]) -> None:
    raise LatentCrateError(f"{name} is not ported yet; run: bash bin/latentcrate {name} ...")


# -- The table -----------------------------------------------------------------

SETS = "<set> [<set> ...] | all"
RUN = {"--sage": SAGE, "--detach": SWITCH, "--timeout": POSITIVE, "--model-set": MODEL_SET}
COMMANDS = (
    Command("init", "", "Create .env and the host data directories.", {}, cmd_init),
    Command(
        "doctor",
        "[--allow-no-gpu] [--sage mode]",
        "Check the host (engine, GPU, directories, ports) before building.",
        {"--allow-no-gpu": SWITCH, "--sage": SAGE},
        cmd_doctor,
    ),
    Command(
        "up",
        "[--sage mode] [--detach] [--timeout seconds] [--model-set name]... "
        "[--frontend-dist dir | --frontend-source dir]",
        "Build the image if needed, start ComfyUI, and return once it is healthy or has failed.",
        RUN | {"--frontend-dist": DIRECTORY, "--frontend-source": DIRECTORY},
        partial(not_ported, "up"),
        exclusive=(("--frontend-dist", "--frontend-source"),),
    ),
    Command(
        "up --frontend-git",
        "<https-url> <ref> [--sage mode] [--detach] [--timeout seconds] [--model-set name]...",
        "The same, with the frontend built from the named Git reference.",
        RUN | {"--frontend-git": SWITCH},
        partial(not_ported, "up --frontend-git"),
        arguments=(2, 2),
    ),
    Command(
        "build",
        "[--sage mode]",
        "Build the image without starting ComfyUI.",
        {"--sage": SAGE},
        partial(not_ported, "build"),
    ),
    Command(
        "build --frontend-git",
        "<https-url> <ref> [--sage mode]",
        "Build the image with the frontend built from the named Git reference.",
        {"--frontend-git": SWITCH, "--sage": SAGE},
        partial(not_ported, "build --frontend-git"),
        arguments=(2, 2),
    ),
    Command("down", "", "Stop and remove the ComfyUI container.", {}, partial(not_ported, "down")),
    Command("status", "", "Show the ComfyUI container status.", {}, partial(not_ported, "status")),
    Command("logs", "", "Follow the ComfyUI container logs.", {}, partial(not_ported, "logs")),
    Command(
        "smoke-gpu",
        "[--sage mode]",
        "Run the GPU verification suite in the running container and save a report.",
        {"--sage": SAGE},
        partial(not_ported, "smoke-gpu"),
    ),
    Command("models list", "", "List the available pinned model-file sets.", {}, partial(not_ported, "models list")),
    Command(
        "models fetch",
        SETS,
        "Download and verify one or more model sets from Hugging Face.",
        {},
        partial(not_ported, "models fetch"),
        arguments=(1, None),
    ),
    Command(
        "models status",
        f"[--verify] {SETS}",
        "Report the selected files present in the models directory; --verify re-reads their checksums.",
        {"--verify": SWITCH},
        partial(not_ported, "models status"),
        arguments=(1, None),
    ),
    Command(
        "models check",
        f"[--remote] {SETS}",
        "Validate the selected manifests; --remote compares their sizes with Hugging Face.",
        {"--remote": SWITCH},
        partial(not_ported, "models check"),
        arguments=(1, None),
    ),
    Command(
        "models draft",
        "hf:<owner>/<repo>:<file>",
        "Print a manifest entry for one Hugging Face file, with its size and checksum.",
        {},
        partial(not_ported, "models draft"),
        arguments=(1, 1),
    ),
    Command(
        "nodes list",
        "",
        "List the available custom-node sets with their descriptions.",
        {},
        partial(not_ported, "nodes list"),
    ),
    Command(
        "nodes install",
        "<set>",
        "Install a pinned custom-node set (stop ComfyUI first).",
        {},
        partial(not_ported, "nodes install"),
        arguments=(1, 1),
    ),
    Command(
        "nodes sync",
        "<set>",
        "Install the set, replacing clean checkouts pinned at other commits.",
        {},
        partial(not_ported, "nodes sync"),
        arguments=(1, 1),
    ),
    Command(
        "nodes status",
        "<set>",
        "Report how the managed third-party nodes compare to the set.",
        {},
        partial(not_ported, "nodes status"),
        arguments=(1, 1),
    ),
    Command(
        "nodes check",
        "<set>",
        "Validate the set's manifest against the allowlist without touching the checkouts.",
        {},
        partial(not_ported, "nodes check"),
        arguments=(1, 1),
    ),
    Command(
        "nodes export",
        "<set>",
        "Print a manifest for the clean public checkouts under the managed custom nodes.",
        {},
        partial(not_ported, "nodes export"),
        arguments=(1, 1),
    ),
    Command(
        "templates list",
        "",
        "List official templates marked for local ComfyUI use in the built image.",
        {},
        partial(not_ported, "templates list"),
    ),
    Command(
        "templates create-model-set",
        "<template> [--name name]",
        "Create a reviewable model-set manifest draft from a template.",
        {"--name": NAME},
        partial(not_ported, "templates create-model-set"),
        arguments=(1, 1),
    ),
    Command("pins", "", "Print the pinned components from pins.env.", {}, cmd_pins),
    Command(
        "pins check",
        "[component|all]",
        "Print the latest eligible upstream pins next to the current ones; nothing is written.",
        {},
        cmd_pins_check,
        arguments=(0, 1),
    ),
)
BY_NAME = {command.name: command for command in COMMANDS}

USAGE = (
    "LatentCrate - pinned ComfyUI containers for Docker and Podman\n\nUsage:\n"
    + "".join(f"  {command.usage}\n      {command.summary}\n" for command in COMMANDS)
    + "\nSettings come from .env.example, overridden by .env. Set\n"
    "CONTAINER_ENGINE=docker or CONTAINER_ENGINE=podman to override detection.\n"
    "Sage modes (LATENTCRATE_SAGE in .env, or --sage for one command):\n"
    "  off        smaller image without SageAttention (the default)\n"
    "  available  Sage-capable image; workflows opt in\n"
    "  global     Sage-capable image; ComfyUI applies Sage globally\n"
)


# -- Entry ---------------------------------------------------------------------


def dispatch(argv: list[str]) -> int:
    command, arguments = resolve(argv)
    options, names = parse(command, arguments)
    return command.handler(Context(PROJECT_ROOT), options, names) or 0


def install_signal_handlers() -> None:
    """A hang-up or termination ends the wrapper the way an exception does,
    so its `with` and `try` blocks unwind and the Compose client is killed.
    A disposition of SIG_IGN, which `nohup` and `setsid` leave behind, is
    respected: a backgrounded `up` or `models fetch` keeps running."""

    def raise_exit(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    for signum in (signal.SIGHUP, signal.SIGTERM):
        if signal.getsignal(signum) is not signal.SIG_IGN:
            signal.signal(signum, raise_exit)


def main(argv: list[str]) -> int:
    install_signal_handlers()
    try:
        if not argv or argv[0] in ("help", "--help", "-h"):
            sys.stdout.write(USAGE)
            status = 0
        else:
            status = dispatch(argv)
        # A listing fits the pipe buffer, so a reader that is gone is first
        # seen here; left to interpreter shutdown it is an "Exception
        # ignored" line and exit status 120.
        sys.stdout.flush()
        return status
    except LatentCrateError as error:
        warn(str(error))
        return 1
    except BrokenPipeError:
        # Before the OSError clause, of which this is a subclass: a reader
        # that closed the pipe (`| head`) is not a filesystem failure.
        with contextlib.suppress(OSError):
            sys.stdout = io.TextIOWrapper(open(1, "wb", closefd=False))
        return 1
    except OSError as error:
        warn(f"filesystem operation failed: {error}")
        return 1
    except KeyboardInterrupt:
        return 130
