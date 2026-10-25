#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Adopt the team's lint baseline in one repository, with check green at once.

For each ecosystem the repository uses (Python, TypeScript or Angular, .NET,
Rust), the script:

  1. applies the baseline config from templates/lint/;
  2. runs the safe autofixes only;
  3. suppresses everything that remains, so `check` passes the same day;
  4. runs the formatter only when the repository has no open pull requests,
     because a formatter rewrites whole files and conflicts with open branches.

It works in the working tree and commits nothing, so the result is one
mechanical pull request for a person to open. It refuses a dirty tree, and it
skips an ecosystem whose own lint config would be overwritten, saying what to
merge by hand. The tools must already be installed: ruff and ty (or uv), the
repository's npm packages, the .NET SDK, cargo with clippy and rustfmt.

From then on only new code must be clean. Burn the suppressions down in
dedicated cleanup pull requests, never inside a feature pull request.

Examples:
  adopt_lint.py ~/dev/orders-api --plan
  adopt_lint.py ~/dev/orders-api
  adopt_lint.py ~/dev/orders-web --only ts --format no
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("adopt_lint.py needs Python 3.11 or later; run it with `uv run`.")

import tomllib

TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "lint"
MARK = "Team lint baseline from team-skills templates/lint"
BEGIN = "# >>> team lint baseline (adopt_lint.py)"
END = "# <<< team lint baseline"
ECOSYSTEMS = ("python", "ts", "dotnet", "rust")
SUPPRESS_ROUNDS = 3
ESLINT_PACKAGES = ("eslint", "@eslint/js", "typescript-eslint", "eslint-config-prettier")
DOTNET_DIAGNOSTIC_RE = re.compile(r":\s+(?:error|warning)\s+([A-Z]{2,}\d{3,})\s*:")
# A lint name, as opposed to a compiler error code such as E0308.
RUST_LINT_RE = re.compile(r"^(?:(?:clippy|rustdoc)::)?[a-z0-9_]+$")
ESLINT_MIN = (9, 24)


class AdoptError(Exception):
    """Something that stops one ecosystem's adoption."""


@dataclass
class Report:
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    follow_up: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Repo:
    path: Path
    format_allowed: bool
    plan: bool
    keep_config: bool

    def run(self, *command: str, ok: bool = False) -> subprocess.CompletedProcess[str]:
        done = subprocess.run(
            list(command), cwd=self.path, capture_output=True, text=True, check=False
        )
        if ok and done.returncode != 0:
            tail = (done.stdout + done.stderr).strip().splitlines()[-15:]
            raise AdoptError(f"`{' '.join(command)}` failed:\n  " + "\n  ".join(tail))
        return done

    def text(self, name: str) -> str:
        path = self.path / name
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def tracked(self, *patterns: str) -> list[str]:
        return self.run("git", "ls-files", "--", *patterns).stdout.split()


# Shared helpers


def count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def ours(path: Path) -> bool:
    """True when the file is missing or came from this script."""
    return not path.exists() or MARK in path.read_text(encoding="utf-8")


def foreign(repo: Repo, what: str, template: str) -> bool:
    """True when the repository's own config should stay; raise unless --keep-config."""
    if not repo.keep_config:
        raise AdoptError(
            f"{what} exists; merge {template} into it by hand, then rerun with --keep-config"
        )
    return True


def copy_template(repo: Repo, source: str, target: str) -> None:
    if not repo.plan:
        shutil.copyfile(TEMPLATES / source, repo.path / target)


def replace_block(text: str, block: str) -> str:
    """Put the marked block at the end of the text, replacing an earlier one."""
    start = text.find(BEGIN)
    if start != -1:
        end = text.find(END, start)
        if end == -1:
            raise AdoptError(f"found {BEGIN!r} with no {END!r}; fix the file by hand")
        text = text[:start] + text[end + len(END) :].lstrip("\n")
    body = text.rstrip("\n")
    return f"{body}\n\n{BEGIN}\n{block.rstrip()}\n{END}\n" if body else f"{BEGIN}\n{block}\n{END}\n"


def tool(name: str) -> list[str]:
    if shutil.which(name):
        return [name]
    if shutil.which("uvx"):
        return ["uvx", name]
    raise AdoptError(f"{name} is not installed, and neither is uv")


def open_pull_requests(path: Path) -> int | None:
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    agent_cli = os.environ.get("AGENT_CLI") or shutil.which("agent-cli")
    if remote.returncode != 0 or agent_cli is None:
        return None
    name = remote.stdout.strip().rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
    done = subprocess.run(
        [
            agent_cli,
            "ado",
            "pr",
            "list",
            "--repo",
            name,
            "--status",
            "active",
            "--fields",
            "id",
            "--limit",
            "500",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        return None
    try:
        rows = json.loads(done.stdout)
    except json.JSONDecodeError:
        return None
    return len(rows) if isinstance(rows, list) else None


# Python, with ruff and ty


def python(repo: Repo, report: Report) -> None:
    pyproject = tomllib.loads(repo.text("pyproject.toml") or "")
    tools = pyproject.get("tool", {})
    kept = set()
    for name, files in (("ruff", ("ruff.toml", ".ruff.toml")), ("ty", ("ty.toml",))):
        template = f"templates/lint/python/{name}.toml"
        if name in tools and foreign(repo, f"[tool.{name}] in pyproject.toml", template):
            kept.add(name)
        for file in files:
            if not ours(repo.path / file) and foreign(repo, file, template):
                kept.add(name)
    if pyproject and not (repo.path / ".venv").is_dir() and "VIRTUAL_ENV" not in os.environ:
        # Without the environment, ty would suppress every unresolved import.
        raise AdoptError(
            "sync the project's environment first (uv sync), so ty can resolve imports"
        )
    applied = [f"{name}.toml" for name in ("ruff", "ty") if name not in kept]
    configs = ", ".join(
        [f"apply {name}" for name in applied]
        + [f"keep the repository's {name} config" for name in sorted(kept)]
    )
    if repo.plan:
        report.done.append(f"python: would {configs}, fix, and suppress")
        return
    for name in applied:
        copy_template(repo, f"python/{name}", name)
    ruff, ty = tool("ruff"), tool("ty")
    repo.run(*ruff, "check", "--fix", "--no-unsafe-fixes", "--exit-zero", ".")
    if repo.format_allowed:
        repo.run(*ruff, "format", ".", ok=True)
        report.done.append("python: formatted with ruff format")
    repo.run(*ruff, "check", "--add-noqa", ".")
    repo.run(*ty, "check", "--add-ignore")
    repo.run(*ruff, "check", ".", ok=True)
    repo.run(*ty, "check", ok=True)
    report.done.append(
        f"python: {configs.replace('apply ', 'applied ').replace('keep ', 'kept ')}; safe fixes made;"
        " the rest has noqa or ty: ignore"
    )
    report.checks.append("ruff check . && ty check")


# TypeScript and Angular, with ESLint and strict compiler options


def strip_jsonc(text: str) -> str:
    """Drop // and /* */ comments and trailing commas, leaving strings intact."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            i = text.find("\n", i) if "\n" in text[i:] else n
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        else:
            out.append(c)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def jsonc(text: str, where: str) -> dict[str, object]:
    try:
        value = json.loads(strip_jsonc(text))
    except json.JSONDecodeError as err:
        raise AdoptError(f"{where} does not parse: {err}") from err
    if not isinstance(value, dict):
        raise AdoptError(f"{where} is not a JSON object")
    return value


def set_compiler_options(text: str, options: dict[str, bool]) -> str:
    """Set boolean compilerOptions in place, keeping the file's comments."""
    opening = re.search(r'"compilerOptions"\s*:\s*\{', text)
    if opening is None:
        raise AdoptError("tsconfig.json has no compilerOptions object; add one by hand")
    inserts = []
    for name, value in options.items():
        literal = "true" if value else "false"
        pattern = re.compile(rf'("{re.escape(name)}"\s*:\s*)(true|false)')
        if pattern.search(text):
            text = pattern.sub(rf"\g<1>{literal}", text, count=1)
        else:
            inserts.append(f'\n    "{name}": {literal},')
    at = opening.end()
    return text[:at] + "".join(inserts) + text[at:]


def node_version(repo: Repo, package: str) -> tuple[int, ...] | None:
    manifest = repo.path / "node_modules" / package / "package.json"
    if not manifest.is_file():
        return None
    version = json.loads(manifest.read_text(encoding="utf-8")).get("version", "0")
    return tuple(int(p) for p in re.findall(r"\d+", str(version))[:3])


def typescript(repo: Repo, report: Report) -> None:
    package = jsonc(repo.text("package.json"), "package.json")
    deps: dict[str, object] = {}
    for key in ("dependencies", "devDependencies"):
        section = package.get(key)
        if isinstance(section, dict):
            deps.update(section)
    kind = "angular" if "@angular/core" in deps else "typescript"
    wanted = (*ESLINT_PACKAGES, "angular-eslint") if kind == "angular" else ESLINT_PACKAGES
    configs = [*repo.path.glob("eslint.config.*"), *repo.path.glob(".eslintrc*")]
    theirs = [p.name for p in configs if not ours(p)]
    keep = bool(theirs) and foreign(
        repo, ", ".join(theirs), f"templates/lint/{kind}/eslint.config.mjs"
    )
    missing = [p for p in wanted if node_version(repo, p) is None]
    if missing:
        raise AdoptError(
            f"install the ESLint packages first: npm install --save-dev {' '.join(missing)}"
        )
    eslint = node_version(repo, "eslint") or ()
    if eslint < ESLINT_MIN:
        raise AdoptError("ESLint 9.24 or later is needed for bulk suppressions; update it first")
    config = "kept the repository's ESLint config" if keep else f"{kind} ESLint config applied"
    if repo.plan:
        report.done.append(f"ts: would apply strict compiler options; {config}")
        return
    if not keep:
        copy_template(repo, f"{kind}/eslint.config.mjs", "eslint.config.mjs")
    repo.run("npx", "eslint", "--fix", ".")
    repo.run("npx", "eslint", "--suppress-all", ".")
    repo.run("npx", "eslint", ".", ok=True)
    report.done.append(f"ts: {config}; safe fixes made; the rest in eslint-suppressions.json")
    strict_compiler_options(repo, report, kind)
    if repo.format_allowed and node_version(repo, "prettier") is not None:
        repo.run("npx", "prettier", "--write", ".", ok=True)
        report.done.append("ts: formatted with prettier")
    report.checks.append("npx eslint . && npx tsc --noEmit -p tsconfig.json")
    if kind == "angular":
        report.follow_up.append(
            "ts: angularCompilerOptions (templates/lint/angular/tsconfig.angular-strict.json) need `ng build`; "
            "turn them on in a cleanup pull request"
        )


def strict_compiler_options(repo: Repo, report: Report, kind: str) -> None:
    """Turn on each strict option that compiles; list the ones that do not."""
    path = repo.path / "tsconfig.json"
    original = path.read_text(encoding="utf-8")
    template = jsonc(
        (TEMPLATES / kind / "tsconfig.strict.json").read_text(encoding="utf-8"), "template"
    )
    options_value = template.get("compilerOptions")
    if not isinstance(options_value, dict):
        raise AdoptError("the tsconfig template has no compilerOptions")
    wanted = {str(k): bool(v) for k, v in options_value.items()}
    current = jsonc(original, "tsconfig.json").get("compilerOptions")
    current = current if isinstance(current, dict) else {}
    todo = {k: v for k, v in wanted.items() if current.get(k) != v}
    projects = [p for p in ("tsconfig.app.json", "tsconfig.spec.json") if (repo.path / p).is_file()]
    projects = projects or ["tsconfig.json"]

    def compiles() -> bool:
        return all(repo.run("npx", "tsc", "--noEmit", "-p", p).returncode == 0 for p in projects)

    if not compiles():
        raise AdoptError("tsc fails before any change; fix the build first")
    path.write_text(set_compiler_options(original, todo), encoding="utf-8")
    if compiles():
        report.done.append(f"ts: all {count(len(todo), 'strict compiler option')} on")
        return
    kept = {}
    for name, value in todo.items():
        path.write_text(set_compiler_options(original, {**kept, name: value}), encoding="utf-8")
        if compiles():
            kept[name] = value
    path.write_text(set_compiler_options(original, kept), encoding="utf-8")
    left = sorted(set(todo) - set(kept))
    report.done.append(f"ts: {len(kept)} of {len(todo)} strict compiler options on")
    report.follow_up.append(
        f"ts: these compiler options still fail and stay off: {', '.join(left)}"
    )


# .NET, with analyzers, .editorconfig severities, and dotnet format


def dotnet_target(repo: Repo) -> str:
    """The solution at the root, or else the only project."""
    solutions = sorted(p.name for p in repo.path.iterdir() if p.suffix in {".sln", ".slnx"})
    candidates = solutions or repo.tracked("*.csproj")
    if len(candidates) != 1:
        raise AdoptError(
            f"found {len(candidates)} solutions or projects at the top; build one solution covering them first"
        )
    return candidates[0]


def dotnet(repo: Repo, report: Report) -> None:
    props = repo.path / "Directory.Build.props"
    keep = not ours(props) and foreign(
        repo, "Directory.Build.props", "templates/lint/dotnet/Directory.Build.props"
    )
    if repo.plan:
        report.done.append(
            "dotnet: would "
            + ("keep Directory.Build.props" if keep else "apply Directory.Build.props")
            + " and add the .editorconfig analyzer block"
        )
        return
    target = dotnet_target(repo)
    repo.run("dotnet", "build", target, "-nologo", ok=True)
    if not keep:
        copy_template(repo, "dotnet/Directory.Build.props", "Directory.Build.props")
    baseline = (TEMPLATES / "dotnet" / "editorconfig").read_text(encoding="utf-8")
    editorconfig = repo.path / ".editorconfig"
    existing = repo.text(".editorconfig") or "root = true\n"

    def write(suppressed: set[str]) -> None:
        lines = [f"dotnet_diagnostic.{d}.severity = suggestion" for d in sorted(suppressed)]
        extra = (
            "\n# Suppressed at adoption; burn these down in cleanup pull requests.\n"
            if lines
            else ""
        )
        editorconfig.write_text(
            replace_block(existing, baseline + extra + "\n".join(lines)), encoding="utf-8"
        )

    write(set())
    repo.run("dotnet", "format", target, "style", "--severity", "warn")
    repo.run("dotnet", "format", target, "analyzers", "--severity", "warn")
    if repo.format_allowed:
        repo.run("dotnet", "format", target, "whitespace", ok=True)
        report.done.append("dotnet: formatted with dotnet format whitespace")
    suppressed: set[str] = set()
    for round_number in range(SUPPRESS_ROUNDS + 1):
        build = repo.run("dotnet", "build", target, "-nologo", "--no-incremental")
        if build.returncode == 0:
            break
        found = set(DOTNET_DIAGNOSTIC_RE.findall(build.stdout + build.stderr)) - suppressed
        if not found or round_number == SUPPRESS_ROUNDS:
            raise AdoptError(
                "dotnet build still fails with nothing left to suppress:\n" + build.stdout[-2000:]
            )
        suppressed |= found
        write(suppressed)
    report.done.append(
        f"dotnet: analyzers on, warnings fail the build; {count(len(suppressed), 'rule')} set to suggestion in .editorconfig"
    )
    report.checks.append(f"dotnet build {target} -nologo")
    if suppressed:
        report.follow_up.append(
            "dotnet: a rule set to suggestion is off for new code too; burn these down first"
        )


# Rust, with clippy and rustc lint tables in Cargo.toml


def lint_tables(prefix: str, allowed: set[str]) -> str:
    base = tomllib.loads((TEMPLATES / "rust" / "lints.toml").read_text(encoding="utf-8"))
    tables: dict[str, dict[str, object]] = {k: dict(v) for k, v in base.items()}
    for code in sorted(allowed):
        group, _, name = code.rpartition("::")
        tables.setdefault(group or "rust", {})[name] = "allow"
    out = []
    for group, entries in tables.items():
        out.append(f"[{prefix}.{group}]")
        for name, value in entries.items():
            if isinstance(value, dict):
                inner = ", ".join(f"{k} = {json.dumps(v)}" for k, v in value.items())
                out.append(f"{name} = {{ {inner} }}")
            else:
                out.append(f"{name} = {json.dumps(value)}")
        out.append("")
    return "\n".join(out).rstrip()


def clippy_codes(repo: Repo) -> set[str]:
    done = repo.run("cargo", "clippy", "--all-targets", "--message-format=json")
    codes = set()
    for line in done.stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        inner = message.get("message") if message.get("reason") == "compiler-message" else None
        code = (inner or {}).get("code") or {}
        name = str(code.get("code") or "")
        if inner and inner.get("level") in {"warning", "error"} and RUST_LINT_RE.match(name):
            codes.add(name)
    return codes


def rust(repo: Repo, report: Report) -> None:
    manifest = repo.path / "Cargo.toml"
    original = manifest.read_text(encoding="utf-8")
    outside = original.split(BEGIN, 1)[0]
    if re.search(r"^\[(workspace\.)?lints", outside, re.MULTILINE):
        # The allow-list goes into the lint tables, which cannot be split in two.
        raise AdoptError(
            "Cargo.toml already has lint tables, which this script cannot add to; merge "
            "templates/lint/rust/lints.toml by hand and allow what still warns there"
        )
    workspace = re.search(r"^\[workspace\]", original, re.MULTILINE) is not None
    prefix = "workspace.lints" if workspace else "lints"
    if repo.plan:
        report.done.append(
            f"rust: would add [{prefix}.*] tables to Cargo.toml, fix, and allow the rest"
        )
        return
    repo.run("cargo", "clippy", "--all-targets", ok=True)
    if workspace:
        join_workspace_lints(repo)
    manifest.write_text(replace_block(original, lint_tables(prefix, set())), encoding="utf-8")
    repo.run("cargo", "clippy", "--fix", "--allow-dirty", "--allow-staged", "--all-targets")
    if repo.format_allowed:
        repo.run("cargo", "fmt", ok=True)
        report.done.append("rust: formatted with cargo fmt")
    allowed: set[str] = set()
    for _ in range(SUPPRESS_ROUNDS):
        found = clippy_codes(repo) - allowed
        if not found:
            break
        allowed |= found
        manifest.write_text(replace_block(original, lint_tables(prefix, allowed)), encoding="utf-8")
    repo.run("cargo", "clippy", "--all-targets", "--", "-D", "warnings", ok=True)
    report.done.append(
        f"rust: [{prefix}.*] tables added; safe fixes made; {count(len(allowed), 'lint')} allowed"
    )
    report.checks.append("cargo clippy --all-targets -- -D warnings")
    if allowed:
        report.follow_up.append(
            "rust: an allowed lint is off for new code too; burn these down first"
        )


def join_workspace_lints(repo: Repo) -> None:
    meta = json.loads(
        repo.run("cargo", "metadata", "--no-deps", "--format-version", "1", ok=True).stdout
    )
    root = (repo.path / "Cargo.toml").resolve()
    for package in meta.get("packages", []):
        member = Path(package["manifest_path"]).resolve()
        if member == root:
            continue
        text = member.read_text(encoding="utf-8")
        if re.search(r"^\[lints\]", text.split(BEGIN, 1)[0], re.MULTILINE):
            continue
        member.write_text(replace_block(text, "[lints]\nworkspace = true"), encoding="utf-8")


# Driver


def detect(repo: Repo) -> list[str]:
    found = []
    if repo.tracked("*.py") or (repo.path / "pyproject.toml").is_file():
        found.append("python")
    package = repo.text("package.json")
    if package and '"typescript"' in package and (repo.path / "tsconfig.json").is_file():
        found.append("ts")
    if repo.tracked("*.csproj", "*.sln", "*.slnx"):
        found.append("dotnet")
    if (repo.path / "Cargo.toml").is_file():
        found.append("rust")
    return found


ADOPT: dict[str, Callable[[Repo, Report], None]] = {
    "python": python,
    "ts": typescript,
    "dotnet": dotnet,
    "rust": rust,
}


def decide_format(mode: str, path: Path, report: Report) -> bool:
    if mode != "auto":
        return mode == "yes"
    count = open_pull_requests(path)
    if count is None:
        report.skipped.append(
            "formatters: could not count open pull requests through agent-cli; not formatting"
        )
        return False
    if count:
        report.skipped.append(
            f"formatters: {count} open pull requests; format in a later pull request"
        )
        return False
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "repo", nargs="?", type=Path, default=Path.cwd(), help="default: the current directory"
    )
    parser.add_argument("--only", help=f"comma-separated subset of {', '.join(ECOSYSTEMS)}")
    parser.add_argument(
        "--format",
        choices=("auto", "yes", "no"),
        default="auto",
        help="auto: only with no open PRs",
    )
    parser.add_argument("--plan", action="store_true", help="say what would happen; change nothing")
    parser.add_argument(
        "--keep-config",
        action="store_true",
        help="keep lint configs the repository already has; still fix and suppress",
    )
    parser.add_argument("--allow-dirty", action="store_true", help="run on uncommitted changes")
    args = parser.parse_args(argv)
    path = args.repo.expanduser().resolve()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=path, capture_output=True, text=True, check=False
    )
    if status.returncode != 0:
        print(f"adopt_lint.py: {path} is not a git repository", file=sys.stderr)
        return 2
    if status.stdout.strip() and not (args.allow_dirty or args.plan):
        print(
            "adopt_lint.py: the working tree has changes; commit or stash them first",
            file=sys.stderr,
        )
        return 2
    report = Report()
    format_allowed = False if args.plan else decide_format(args.format, path, report)
    repo = Repo(path, format_allowed, args.plan, args.keep_config)
    found = detect(repo)
    if args.only:
        only = {s.strip() for s in args.only.split(",")}
        unknown = only - set(ECOSYSTEMS)
        if unknown:
            parser.error(f"unknown ecosystems: {', '.join(sorted(unknown))}")
        found = [e for e in found if e in only]
    if not found:
        print(
            "adopt_lint.py: found no Python, TypeScript, .NET, or Rust code to lint",
            file=sys.stderr,
        )
        return 1
    for ecosystem in found:
        try:
            ADOPT[ecosystem](repo, report)
        except AdoptError as err:
            report.skipped.append(f"{ecosystem}: {err}")
    print_report(report)
    return 1 if report.skipped and not report.done else 0


def print_report(report: Report) -> None:
    for title, lines in (
        ("Done", report.done),
        ("Skipped", report.skipped),
        ("Follow up", report.follow_up),
    ):
        if lines:
            print(f"{title}:")
            print("\n".join(f"  - {line}" for line in lines))
    if report.checks:
        print("check (record it under ## Commands in AGENTS.md):")
        print(f"  {' && '.join(report.checks)}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
