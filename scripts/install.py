#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Install the skills by symlinking each one into ~/.agents/skills/.

Cursor, Copilot in VS Code, and Copilot CLI all read skills from
~/.agents/skills/, so one link per skill serves all three, and `git pull`
updates them. The script also removes links to skills this repository no
longer has. It never replaces a real folder, and replaces a link that points
somewhere else only with --force.

--windows also copies the skills to %USERPROFILE%\\.agents\\skills, for an
editor that reads the Windows profile rather than the WSL home. Windows cannot
follow WSL links, so rerun it after each `git pull`.

Examples:
  install.py
  install.py --windows
  install.py --uninstall
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("install.py needs Python 3.11 or later; run it with `uv run`.")

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
MARKER = ".installed-from-team-skills"


class InstallError(Exception):
    pass


def skills() -> list[Path]:
    return sorted(p for p in SKILLS.iterdir() if (p / "SKILL.md").is_file())


def points_into_repo(link: Path) -> bool:
    target = link.readlink()
    target = target if target.is_absolute() else link.parent / target
    return target.resolve(strict=False).is_relative_to(SKILLS)


def link_all(dest: Path, *, force: bool, dry_run: bool) -> list[str]:
    report = []
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
    for skill in skills():
        link = dest / skill.name
        if link.is_symlink():
            if link.resolve() == skill:
                report.append(f"ok       {link}")
                continue
            if not force:
                report.append(f"skipped  {link} points to {link.readlink()}; --force replaces it")
                continue
            if not dry_run:
                link.unlink()
        elif link.exists():
            report.append(f"skipped  {link} is a real folder; move it away first")
            continue
        if not dry_run:
            link.symlink_to(skill, target_is_directory=True)
        report.append(f"linked   {link} -> {skill}")
    if dest.is_dir():
        report += prune(dest, dry_run=dry_run)
    return report


def prune(dest: Path, *, dry_run: bool) -> list[str]:
    report = []
    for link in sorted(dest.iterdir()):
        if link.is_symlink() and points_into_repo(link) and not link.exists():
            if not dry_run:
                link.unlink()
            report.append(f"removed  {link} (the skill is gone)")
    return report


def unlink_all(dest: Path, *, dry_run: bool) -> list[str]:
    report = []
    if not dest.is_dir():
        return report
    for link in sorted(dest.iterdir()):
        if link.is_symlink() and points_into_repo(link):
            if not dry_run:
                link.unlink()
            report.append(f"removed  {link}")
    return report


def windows_skills_dir() -> Path:
    try:
        profile = subprocess.run(
            ["cmd.exe", "/c", "echo %USERPROFILE%"],
            capture_output=True,
            text=True,
            check=True,
            cwd="/mnt/c",
        ).stdout.strip()
        unix = subprocess.run(
            ["wslpath", "-u", profile], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as err:
        raise InstallError(f"cannot find the Windows profile from WSL: {err}") from err
    return Path(unix) / ".agents" / "skills"


def copy_all(dest: Path, *, dry_run: bool, remove: bool) -> list[str]:
    report = []
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
    wanted = {s.name for s in skills()}
    for folder in sorted(dest.iterdir()) if dest.is_dir() else []:
        ours = (folder / MARKER).is_file()
        if ours and (remove or folder.name not in wanted):
            if not dry_run:
                shutil.rmtree(folder)
            report.append(f"removed  {folder}")
    if remove:
        return report
    for skill in skills():
        target = dest / skill.name
        if target.exists() and not (target / MARKER).is_file():
            report.append(f"skipped  {target} was not installed by this script")
            continue
        if not dry_run:
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(skill, target, ignore=shutil.ignore_patterns("__pycache__"))
            (target / MARKER).write_text(f"{skill}\n", encoding="utf-8")
        report.append(f"copied   {skill.name} -> {target}")
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--uninstall", action="store_true", help="remove what this installed")
    parser.add_argument("--windows", action="store_true", help="also copy to the Windows profile")
    parser.add_argument("--force", action="store_true", help="replace links pointing elsewhere")
    parser.add_argument("--dry-run", action="store_true", help="report only; change nothing")
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path.home() / ".agents" / "skills",
        help="where to link the skills (default: ~/.agents/skills)",
    )
    args = parser.parse_args(argv)
    try:
        if args.uninstall:
            report = unlink_all(args.dir, dry_run=args.dry_run)
        else:
            report = link_all(args.dir, force=args.force, dry_run=args.dry_run)
        if args.windows:
            report += copy_all(windows_skills_dir(), dry_run=args.dry_run, remove=args.uninstall)
    except (InstallError, OSError) as err:
        print(f"install.py: {err}", file=sys.stderr)
        return 1
    print("\n".join(report) if report else "nothing to do")
    return 1 if any(line.startswith("skipped") for line in report) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
