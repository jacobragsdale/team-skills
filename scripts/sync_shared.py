#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Copy shared snippets into the skills that use them.

shared/sync.toml lists each shared file, where it goes inside a skill, and
which skills carry it. An installed skill cannot reach the rest of this
repository, so each one gets its own copy. Edit the source, never a copy,
then run this script. --check only reports copies that differ.

Examples:
  sync_shared.py
  sync_shared.py --check
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("sync_shared.py needs Python 3.11 or later; run it with `uv run`.")

import tomllib

ROOT = Path(__file__).resolve().parent.parent


class SyncError(Exception):
    pass


@dataclass(frozen=True)
class Copy:
    source: Path
    target: Path


def strings(value: object, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise SyncError(f"{where} must be a list of texts")
    return [str(v) for v in value]


def copies(root: Path) -> list[Copy]:
    manifest = root / "shared" / "sync.toml"
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise SyncError(f"{manifest}: {err}") from err
    entries = data.get("copy")
    if not isinstance(entries, list):
        raise SyncError(f"{manifest}: expected [[copy]] tables")
    out = []
    for index, entry in enumerate(entries, 1):
        where = f"{manifest}: copy #{index}"
        if not isinstance(entry, dict):
            raise SyncError(f"{where} must be a table")
        source, to = entry.get("from"), entry.get("to")
        if not isinstance(source, str) or not isinstance(to, str):
            raise SyncError(f"{where} needs `from` and `to`")
        source_path = root / source
        if not source_path.is_file():
            raise SyncError(f"{where}: {source} does not exist")
        for skill in strings(entry.get("skills"), f"{where}: skills"):
            skill_dir = root / "skills" / skill
            if not (skill_dir / "SKILL.md").is_file():
                raise SyncError(f"{where}: skills/{skill}/SKILL.md does not exist")
            out.append(Copy(source_path, skill_dir / to))
    return out


def drifted(copy: Copy) -> bool:
    return not copy.target.is_file() or copy.target.read_bytes() != copy.source.read_bytes()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="report drift; change nothing")
    args = parser.parse_args(argv)
    try:
        planned = copies(ROOT)
    except SyncError as err:
        print(f"sync_shared.py: {err}", file=sys.stderr)
        return 1
    stale = [c for c in planned if drifted(c)]
    for copy in stale:
        target = copy.target.relative_to(ROOT)
        source = copy.source.relative_to(ROOT)
        if args.check:
            print(f"{target} differs from {source}")
            continue
        copy.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(copy.source, copy.target)
        print(f"{source} -> {target}")
    if args.check and stale:
        return 1
    if not stale:
        print(f"all {len(planned)} copies match their sources")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
