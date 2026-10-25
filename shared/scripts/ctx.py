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

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("ctx.py needs Python 3.11 or later; run it with `uv run`.")

import tomllib

HEADING_RE = re.compile(r"^##\s+Environments\s*$")
FENCE_OPEN_RE = re.compile(r"^```toml\s*$")
FENCE_CLOSE_RE = re.compile(r"^```\s*$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

Scalar = str | int | float | bool


class ContextError(Exception):
    pass


@dataclass(frozen=True)
class Domain:
    name: str
    flags: dict[str, Scalar]
    names: dict[str, list[str]]

    def flag_line(self) -> str:
        parts = []
        for key, value in self.flags.items():
            shown = str(value).lower() if isinstance(value, bool) else str(value)
            parts.append(f"--{key} {shlex.quote(shown)}")
        return " ".join(parts)


@dataclass(frozen=True)
class Environment:
    name: str
    facts: dict[str, Scalar | list[str]]
    domains: list[Domain]


def repo_file() -> Path:
    done = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise ContextError("not inside a git repository; pass --file")
    return Path(done.stdout.strip()) / "docs" / "app-context.md"


def toml_block(markdown: str, path: Path) -> str:
    lines = markdown.splitlines()
    start = next((i for i, line in enumerate(lines) if HEADING_RE.match(line)), None)
    if start is None:
        raise ContextError(f"{path} has no `## Environments` section")
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
        raise ContextError(f"{path}: the toml block under `## Environments` is never closed")
    raise ContextError(f"{path}: `## Environments` holds no ```toml block")


def scalar_or_names(value: object, where: str) -> Scalar | list[str]:
    if isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [str(v) for v in value]
    raise ContextError(f"{where} must be a text, a number, a boolean, or a list of texts")


def parse(source: str, path: Path) -> list[Environment]:
    try:
        data = tomllib.loads(source)
    except tomllib.TOMLDecodeError as err:
        raise ContextError(f"{path}: the Environments TOML does not parse: {err}") from err
    envs = []
    for env_name, env_value in data.items():
        if not isinstance(env_value, dict):
            raise ContextError(f"{path}: `{env_name}` must be a table, such as [{env_name}]")
        facts: dict[str, Scalar | list[str]] = {}
        domains = []
        for key, value in env_value.items():
            where = f"{path}: {env_name}.{key}"
            if not KEY_RE.match(key):
                raise ContextError(f"{where}: use lowercase letters, digits, - and _")
            if not isinstance(value, dict):
                facts[key] = scalar_or_names(value, where)
                continue
            flags: dict[str, Scalar] = {}
            names: dict[str, list[str]] = {}
            for flag, flag_value in value.items():
                inner = f"{where}.{flag}"
                if not KEY_RE.match(flag):
                    raise ContextError(f"{inner}: use lowercase letters, digits, - and _")
                checked = scalar_or_names(flag_value, inner)
                if isinstance(checked, list):
                    names[flag] = checked
                else:
                    flags[flag] = checked
            domains.append(Domain(key, flags, names))
        envs.append(Environment(env_name, facts, domains))
    if not envs:
        raise ContextError(f"{path}: the Environments TOML names no environment")
    return envs


def load(path: Path) -> list[Environment]:
    try:
        markdown = path.read_text(encoding="utf-8")
    except FileNotFoundError as err:
        raise ContextError(f"{path} does not exist; /dev-context writes it") from err
    return parse(toml_block(markdown, path), path)


def render(env: Environment, only: str | None) -> str:
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
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
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
        payload = {
            "env": env.name,
            "facts": env.facts if args.domain is None else {},
            "domains": {d.name: {"flags": d.flags, "names": d.names} for d in domains},
        }
        json.dump(payload, sys.stdout, indent=1)
        print()
    else:
        print(render(env, args.domain))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
