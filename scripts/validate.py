#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Check this repository against the rules in AGENTS.md.

Skills: the folder is dev-<name> and matches `name`; the frontmatter holds
only simple `key: value` lines; `disable-model-invocation` is true; the
description is one sentence; every bundled file a skill names exists inside
its own folder; and no skill holds a URL.

Shared copies: every copy listed in shared/sync.toml matches its source.

Scripts: every Python file carries a PEP 723 header that asks for Python 3.11
or later and no dependencies, and imports only the standard library. The
repository holds no shell scripts.

Banned words: when ~/.config/team-skills/banned-words.txt exists (or the file
TEAM_SKILLS_BANNED_WORDS names), none of its words, one per line, appear in
skills/, shared/, or templates/. The list stays outside the repository, so
the names it guards never get committed. team.toml is the one file allowed to
hold the team's real names, so the check skips it.

Porting: every line still marked `PORT:` in shared/, scripts/, or a SKILL.md
prints as a warning. Warnings never fail the run; docs/porting.md says how to
clear them at work.

Prints one line per problem and exits 1 when there is any.
"""

from __future__ import annotations

# Before any other import, so an older python3 gets this message, not an ImportError.
import sys

if sys.version_info < (3, 11):
    sys.exit("validate.py needs Python 3.11 or later; run it with `uv run`.")


import argparse
import ast
import os
import re
import shutil
import subprocess
import tomllib
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_NAME_RE = re.compile(r"^dev-[a-z0-9]+(-[a-z0-9]+)*$")
FRONTMATTER_LINE_RE = re.compile(r"^([a-z][a-z0-9_-]*):\s*(.*)$")
FRONTMATTER_KEYS = {"name", "description", "disable-model-invocation", "argument-hint"}
REQUIRED_KEYS = {"name", "description", "disable-model-invocation"}
DESCRIPTION_MAX = 200
BODY_MAX_LINES = 500
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
# Bare bundled paths only: `~/x/scripts/a.py` and `repo/scripts/a.py` are not
# references to the skill's own files.
BUNDLED_RE = re.compile(r"(?<![\w/~.<-])((?:scripts|references|assets)/[A-Za-z0-9_.\-/]+)")
URL_RE = re.compile(r"https?://")
PEP723_RE = re.compile(r"^# /// script\n((?:#.*\n)*?)# ///$", re.MULTILINE)
SHELL_SUFFIXES = {".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd"}
TEXT_SUFFIXES = {".md", ".py", ".toml", ".json", ".yaml", ".yml", ".txt", ".js"}
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules"}
TEAM_FILE = "team.toml"
PORT_RE = re.compile(r"\bPORT:")


def files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and not SKIP_DIRS.intersection(path.parts):
            yield path


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def frontmatter(text: str, where: str, problems: list[str]) -> tuple[dict[str, str], str]:
    lines = text.split("\n")
    if lines[0] != "---" or "---" not in lines[1:]:
        problems.append(f"{where}: starts without a --- frontmatter block")
        return {}, text
    end = lines.index("---", 1)
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        match = FRONTMATTER_LINE_RE.match(line)
        if match is None:
            problems.append(f"{where}: frontmatter line is not `key: value`: {line!r}")
            continue
        key, value = match.groups()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        fields[key] = value.strip()
    return fields, "\n".join(lines[end + 1 :])


def check_description(description: str, where: str, problems: list[str]) -> None:
    if len(description) > DESCRIPTION_MAX:
        problems.append(f"{where}: description is over {DESCRIPTION_MAX} characters")
    if not description.endswith("."):
        problems.append(f"{where}: description should be one sentence ending in a period")
    if re.search(r"[.!?]\s+\S", description):
        problems.append(f"{where}: description holds more than one sentence")


def check_references(skill: Path, problems: list[str]) -> None:
    for path in files(skill):
        # assets/ holds templates for other repositories; their links point there.
        if path.suffix != ".md" or path.relative_to(skill).parts[0] == "assets":
            continue
        text = path.read_text(encoding="utf-8")
        for match in LINK_RE.finditer(text):
            target = match.group(1).split("#", 1)[0]
            if not target or URL_RE.match(target):
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.is_relative_to(skill.resolve()):
                problems.append(f"{rel(path)}: links outside its skill folder: {target}")
            elif not resolved.exists():
                problems.append(f"{rel(path)}: links to a missing file: {target}")
        for match in BUNDLED_RE.finditer(text):
            name = match.group(1).rstrip(".")
            if not (skill / name).exists():
                problems.append(f"{rel(path)}: names {name}, which is not in {rel(skill)}")


def check_skill(skill: Path, problems: list[str]) -> None:
    where = rel(skill / "SKILL.md")
    if not SKILL_NAME_RE.match(skill.name):
        problems.append(f"{rel(skill)}: a skill folder is named dev-<name>")
    if not (skill / "SKILL.md").is_file():
        problems.append(f"{rel(skill)}: has no SKILL.md")
        return
    fields, body = frontmatter((skill / "SKILL.md").read_text(encoding="utf-8"), where, problems)
    problems.extend(f"{where}: frontmatter lacks {key}" for key in sorted(REQUIRED_KEYS - fields.keys()))
    problems.extend(f"{where}: frontmatter key {key} is not one this repository uses" for key in sorted(fields.keys() - FRONTMATTER_KEYS))
    if "name" in fields and fields["name"] != skill.name:
        problems.append(f"{where}: name {fields['name']!r} does not match the folder")
    if fields.get("disable-model-invocation", "true") != "true":
        problems.append(f"{where}: disable-model-invocation must be true")
    if "description" in fields:
        check_description(fields["description"], where, problems)
    if body.count("\n") > BODY_MAX_LINES:
        problems.append(f"{where}: the body is over {BODY_MAX_LINES} lines")
    check_references(skill, problems)
    problems.extend(f"{rel(path)}: holds a URL; a skill names no hosts" for path in files(skill) if path.suffix in TEXT_SUFFIXES and URL_RE.search(path.read_text(encoding="utf-8")))


def check_copies(problems: list[str]) -> None:
    manifest = ROOT / "shared" / "sync.toml"
    try:
        entries = tomllib.loads(manifest.read_text(encoding="utf-8")).get("copy", [])
    except (OSError, tomllib.TOMLDecodeError) as err:
        problems.append(f"{rel(manifest)}: {err}")
        return
    for entry in entries:
        source = ROOT / str(entry.get("from", ""))
        if not source.is_file():
            problems.append(f"{rel(manifest)}: {entry.get('from')} does not exist")
            continue
        for skill in entry.get("skills", []):
            target = ROOT / "skills" / str(skill) / str(entry.get("to", ""))
            if not target.is_file() or target.read_bytes() != source.read_bytes():
                problems.append(f"{rel(target)}: differs from {rel(source)}; run scripts/sync_shared.py")


def check_script(path: Path, problems: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    header = PEP723_RE.search(text)
    if header is None:
        problems.append(f"{rel(path)}: has no PEP 723 `# /// script` header")
    else:
        body = "\n".join(line.removeprefix("#").strip() for line in header.group(1).splitlines())
        try:
            meta = tomllib.loads(body)
        except tomllib.TOMLDecodeError as err:
            problems.append(f"{rel(path)}: the PEP 723 header does not parse: {err}")
            meta = {}
        if meta.get("requires-python") != ">=3.11":
            problems.append(f'{rel(path)}: PEP 723 requires-python should be ">=3.11"')
        if meta.get("dependencies") != []:
            problems.append(f"{rel(path)}: PEP 723 dependencies must be []; stdlib only")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as err:
        problems.append(f"{rel(path)}: does not parse: {err}")
        return
    check_version_guard(tree, path, problems)
    allowed = sys.stdlib_module_names | {"__future__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            names = [node.module]
        else:
            continue
        problems.extend(f"{rel(path)}:{node.lineno}: imports {name}, not the stdlib" for name in names if name.split(".")[0] not in allowed)


def check_version_guard(tree: ast.Module, path: Path, problems: list[str]) -> None:
    """`import sys` and the 3.11 guard come before every other import, so an older python3 reaches the guard."""
    body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom) and n.module == "__future__")]
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]  # the docstring
    first_import = next((i for i, n in enumerate(body) if isinstance(n, ast.Import | ast.ImportFrom) and not (isinstance(n, ast.Import) and [a.name for a in n.names] == ["sys"])), len(body))
    guarded = any(isinstance(n, ast.If) and "version_info" in ast.unparse(n.test) for n in body[:first_import])
    if not guarded:
        problems.append(f"{rel(path)}: the `sys.version_info < (3, 11)` guard must come before every import but `import sys`")


def banned_words() -> list[str]:
    configured = os.environ.get("TEAM_SKILLS_BANNED_WORDS", "")  # set but empty means unset
    path = Path(configured) if configured else Path.home() / ".config" / "team-skills" / "banned-words.txt"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [w.strip() for w in lines if w.strip() and not w.startswith("#")]


def check_banned(problems: list[str]) -> None:
    words = banned_words()
    if not words:
        return
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, words)) + r")\b", re.IGNORECASE)
    for folder in ("skills", "shared", "templates"):
        for path in files(ROOT / folder):
            if path.suffix not in TEXT_SUFFIXES and path.name != ".editorconfig":
                continue
            if path.name == TEAM_FILE:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    problems.append(f"{rel(path)}:{number}: holds a banned word")


def check_repository(problems: list[str]) -> None:
    for path in files(ROOT):
        if path.suffix in SHELL_SUFFIXES:
            problems.append(f"{rel(path)}: shell scripts are not allowed; write Python")
        elif path.suffix == ".py":
            check_script(path, problems)
    skills = {p.name for p in (ROOT / "skills").iterdir() if p.is_dir()}
    fixtures = ROOT / "fixtures"
    if fixtures.is_dir():
        problems.extend(f"{rel(entry)}: names no skill in skills/" for entry in fixtures.iterdir() if entry.is_dir() and entry.name not in skills)


RUFF_PIN = "ruff@0.17.3"  # Matches required-version in templates/lint/python/ruff.toml.
TY_PIN = "ty@0.0.95"  # Matches the ty pin in the python-standards pre-commit config.


def runner(name: str, pin: str) -> list[str] | None:
    """The tool on PATH, or else the pinned release through uvx."""
    if shutil.which(name) is not None:
        return [name]
    if shutil.which("uvx") is not None:
        return ["uvx", pin]
    return None


def check_lint(problems: list[str]) -> None:
    """This repository's own Python passes the team's Ruff and ty baselines."""
    template = tomllib.loads((ROOT / "templates" / "lint" / "python" / "ty.toml").read_text(encoding="utf-8"))
    own = tomllib.loads((ROOT / "ty.toml").read_text(encoding="utf-8"))
    problems.extend(f"ty.toml: [{table}] differs from templates/lint/python/ty.toml" for table in template if own.get(table) != template[table])
    runs = [
        ("ruff", RUFF_PIN, ["check", "--quiet", "."], "ruff check"),
        ("ruff", RUFF_PIN, ["format", "--check", "--quiet", "."], "ruff format --check"),
        # ty checks a PEP 723 script as its own project; --config-file makes ty.toml reach them.
        ("ty", TY_PIN, ["check", "--config-file", "ty.toml", "--output-format", "concise"], "ty check --config-file ty.toml"),
    ]
    for name, pin, args, what in runs:
        tool = runner(name, pin)
        if tool is None:
            problems.append(f"{name} is not installed, and neither is uv")
            continue
        done = subprocess.run([*tool, *args], cwd=ROOT, capture_output=True, text=True, check=False)
        if done.returncode != 0:
            problems.append(f"{what} fails; run it to see why:\n{(done.stdout + done.stderr).strip()}")


def port_markers() -> list[str]:
    """Lines still marked PORT:, in the sources only, not in the skills' synced copies."""
    sources = [*files(ROOT / "shared"), *files(ROOT / "scripts")]
    sources += sorted((ROOT / "skills").glob("*/SKILL.md"))
    out = []
    for path in sources:
        if path.suffix not in TEXT_SUFFIXES or path == Path(__file__).resolve():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PORT_RE.search(line):
                out.append(f"{rel(path)}:{number}: {line.strip()}")
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args(argv)
    problems: list[str] = []
    skills_dir = ROOT / "skills"
    skills = sorted(p for p in skills_dir.iterdir() if p.is_dir()) if skills_dir.is_dir() else []
    for skill in skills:
        check_skill(skill, problems)
    check_copies(problems)
    check_repository(problems)
    check_banned(problems)
    check_lint(problems)
    ports = port_markers()
    for port in ports:
        print(f"warning: {port}")
    if ports:
        print(f"{len(ports)} PORT: markers remain; docs/porting.md clears them", file=sys.stderr)
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} problems", file=sys.stderr)
        return 1
    print(f"{len(skills)} skills valid")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
