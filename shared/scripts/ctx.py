#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Print one environment's agent-cli flags from docs/app-context.md.

The file's `## Environments` section holds one fenced TOML block. Each
environment is a table, and each agent-cli domain inside it is a subtable:

  [qa]
  url = "..."                          # facts about the environment
  [qa.k8s]
  cluster = "qa"                       # a text, number or boolean: a flag
  namespace = "orders"
  deployments = ["orders-api"]         # a list: names that exist there

A text, number or boolean prints as `--key value`, ready to paste after an
agent-cli command in that domain. A list prints as the names to use with it.
The file is found from the current repository unless --file names it.

Examples:
  ctx.py --env qa
  ctx.py --env prod --domain k8s
  ctx.py --list
  ctx.py --check
"""

from __future__ import annotations

# Before any other import, so an older python3 gets this message, not an ImportError.
import sys

if sys.version_info < (3, 11):
    sys.exit("ctx.py needs Python 3.11 or later; run it with `uv run`.")


import argparse
import json
import re
import shlex
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

HEADING_RE = re.compile(r"^##\s+Environments\s*$")
FENCE_OPEN_RE = re.compile(r"^```toml\s*$")
FENCE_CLOSE_RE = re.compile(r"^```\s*$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

Scalar = str | int | float | bool


class ContextError(Exception):
    """Raised when docs/app-context.md is missing, or its Environments block does not parse."""


@dataclass(frozen=True)
class Domain:
    """One agent-cli domain's section in an environment: its flags, and the name lists it holds."""

    name: str
    flags: dict[str, Scalar]
    names: dict[str, list[str]]

    def flag_line(self) -> str:
        """The flags as agent-cli options, shell-quoted, with booleans in lowercase."""
        parts = []
        for key, value in self.flags.items():
            shown = str(value).lower() if isinstance(value, bool) else str(value)
            parts.append(f"--{key} {shlex.quote(shown)}")
        return " ".join(parts)


@dataclass(frozen=True)
class Environment:
    """One environment table: its plain facts, and a Domain per agent-cli domain section."""

    name: str
    facts: dict[str, Scalar | list[str]]
    domains: list[Domain]


def executable(name: str) -> str:
    """The full path of `name` on PATH, so a process never starts from a partial path."""
    found = shutil.which(name)
    if found is None:
        msg = f"{name} is not on PATH"
        raise ContextError(msg)
    return found


def repo_file() -> Path:
    """docs/app-context.md at the top of the current git repository."""
    done = subprocess.run([executable("git"), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        msg = "not inside a git repository; pass --file"
        raise ContextError(msg)
    return Path(done.stdout.strip()) / "docs" / "app-context.md"


def toml_block(markdown: str, path: Path) -> str:
    """The first ```toml block under `## Environments`, without its fences."""
    lines = markdown.splitlines()
    start = next((i for i, line in enumerate(lines) if HEADING_RE.match(line)), None)
    if start is None:
        msg = f"{path} has no `## Environments` section"
        raise ContextError(msg)
    block: list[str] | None = None
    for line in lines[start + 1 :]:
        if block is None:
            if line.startswith("## "):
                break
            if FENCE_OPEN_RE.match(line):
                block = []
        elif FENCE_CLOSE_RE.match(line):
            return "\n".join(block)
        else:
            block.append(line)
    if block is not None:
        msg = f"{path}: the toml block under `## Environments` is never closed"
        raise ContextError(msg)
    msg = f"{path}: `## Environments` holds no ```toml block"
    raise ContextError(msg)


def scalar_or_names(value: object, where: str) -> Scalar | list[str]:
    """Check that a TOML value is a scalar or a list of strings; `where` names it in the error."""
    if isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [str(v) for v in value]
    msg = f"{where} must be a text, a number, a boolean, or a list of texts"
    raise ContextError(msg)


def parse(source: str, path: Path) -> list[Environment]:
    """The environments in the TOML, in file order; a nested table becomes a Domain, anything else a fact."""
    try:
        data = tomllib.loads(source)
    except tomllib.TOMLDecodeError as err:
        msg = f"{path}: the Environments TOML does not parse: {err}"
        raise ContextError(msg) from err
    envs = []
    for env_name, env_value in data.items():
        if not isinstance(env_value, dict):
            msg = f"{path}: `{env_name}` must be a table, such as [{env_name}]"
            raise ContextError(msg)
        facts: dict[str, Scalar | list[str]] = {}
        domains = []
        for key, value in env_value.items():
            where = f"{path}: {env_name}.{key}"
            if not KEY_RE.match(key):
                msg = f"{where}: use lowercase letters, digits, - and _"
                raise ContextError(msg)
            if not isinstance(value, dict):
                facts[key] = scalar_or_names(value, where)
                continue
            flags: dict[str, Scalar] = {}
            names: dict[str, list[str]] = {}
            for flag, flag_value in value.items():
                inner = f"{where}.{flag}"
                if not KEY_RE.match(flag):
                    msg = f"{inner}: use lowercase letters, digits, - and _"
                    raise ContextError(msg)
                checked = scalar_or_names(flag_value, inner)
                if isinstance(checked, list):
                    names[flag] = checked
                else:
                    flags[flag] = checked
            domains.append(Domain(key, flags, names))
        envs.append(Environment(env_name, facts, domains))
    if not envs:
        msg = f"{path}: the Environments TOML names no environment"
        raise ContextError(msg)
    return envs


def load(path: Path) -> list[Environment]:
    """The environments that the file's Environments block declares."""
    try:
        markdown = path.read_text(encoding="utf-8")
    except FileNotFoundError as err:
        msg = f"{path} does not exist; /dev-context writes it"
        raise ContextError(msg) from err
    return parse(toml_block(markdown, path), path)


def render(env: Environment, only: str | None) -> str:
    """The environment as text; with `only`, that domain's section without the facts."""
    out = [f"# {env.name}"]
    if only is None:
        for key, value in env.facts.items():
            shown = ", ".join(value) if isinstance(value, list) else str(value)
            out.append(f"{key}: {shown}")
    for domain in env.domains:
        if only is not None and domain.name != only:
            continue
        out.append(f"{domain.name}: {domain.flag_line()}".rstrip())
        for key, values in domain.names.items():
            out.append(f"  {key}: {', '.join(values)}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    """Print one environment, list them, or check the block; exit 4 for an unknown environment or domain."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--env", help="the environment to print, such as qa")
    action.add_argument("--list", action="store_true", help="list the environments")
    action.add_argument("--check", action="store_true", help="only check that the block parses")
    parser.add_argument("--domain", help="only this agent-cli domain, such as k8s")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    parser.add_argument("--file", type=Path, help="default: docs/app-context.md in this repo")
    args = parser.parse_args(argv)
    try:
        path = args.file or repo_file()
        envs = load(path)
    except ContextError as err:
        print(f"ctx.py: {err}", file=sys.stderr)
        return 1
    if args.check:
        print(f"{path}: {len(envs)} environments: {', '.join(e.name for e in envs)}")
        return 0
    if args.list:
        print("\n".join(e.name for e in envs))
        return 0
    env = next((e for e in envs if e.name == args.env), None)
    if env is None:
        known = ", ".join(e.name for e in envs)
        print(f"ctx.py: no environment {args.env!r} in {path}; it has {known}", file=sys.stderr)
        return 4
    if args.domain is not None and all(d.name != args.domain for d in env.domains):
        print(f"ctx.py: {env.name} has no {args.domain} section", file=sys.stderr)
        return 4
    if args.json:
        domains = [d for d in env.domains if args.domain in (None, d.name)]
        payload = {"env": env.name, "facts": env.facts if args.domain is None else {}, "domains": {d.name: {"flags": d.flags, "names": d.names} for d in domains}}
        json.dump(payload, sys.stdout, indent=1)
        print()
    else:
        print(render(env, args.domain))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
