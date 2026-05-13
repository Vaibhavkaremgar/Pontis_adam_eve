from __future__ import annotations

import os
import re
import subprocess
import sys
import shutil
from pathlib import Path


REVISION_PATTERN = re.compile(r"^\s*revision\s*(?::[^=]+)?=\s*['\"](?P<revision>[^'\"]+)['\"]\s*$", re.MULTILINE)
MAX_REVISION_LENGTH = 16


def _backend_root() -> Path:
    script_path = Path(__file__).resolve()
    override = os.getenv("ALEMBIC_INTEGRITY_BACKEND_DIR", "").strip()
    if override:
        return Path(override).resolve()
    return script_path.parent.parent


def _versions_dir(backend_root: Path) -> Path:
    override = os.getenv("ALEMBIC_INTEGRITY_VERSIONS_DIR", "").strip()
    if override:
        return Path(override).resolve()
    return backend_root / "alembic" / "versions"


def _prepare_env(backend_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "").strip()
    backend_root_str = str(backend_root)
    env["PYTHONPATH"] = backend_root_str if not pythonpath else f"{backend_root_str}{os.pathsep}{pythonpath}"
    env.setdefault("DATABASE_URL", "sqlite:///./alembic_integrity_check.db")
    return env


def _run_alembic_heads(backend_root: Path) -> tuple[int, str, str]:
    env = _prepare_env(backend_root)
    scripts_dir = Path(sys.executable).resolve().parent
    repo_venv_dir = backend_root / "venv"
    repo_venv_alembic = repo_venv_dir / "Scripts" / "alembic.exe"
    repo_venv_alembic_unix = repo_venv_dir / "bin" / "alembic"
    local_alembic = scripts_dir / ("alembic.exe" if os.name == "nt" else "alembic")
    fallback_commands: list[list[str]] = []
    if shutil.which("alembic"):
        fallback_commands.append(["alembic", "heads"])
    if repo_venv_alembic.exists():
        fallback_commands.append([str(repo_venv_alembic), "heads"])
    if repo_venv_alembic_unix.exists():
        fallback_commands.append([str(repo_venv_alembic_unix), "heads"])
    if local_alembic.exists():
        fallback_commands.append([str(local_alembic), "heads"])
    fallback_commands.append([sys.executable, "-m", "alembic", "heads"])
    last_error: FileNotFoundError | None = None
    for command in fallback_commands:
        try:
            result = subprocess.run(
                command,
                cwd=str(backend_root),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            return result.returncode, result.stdout, result.stderr
        except FileNotFoundError as exc:
            last_error = exc
    raise last_error or FileNotFoundError("alembic executable not found")


def _collect_revision_files(versions_dir: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for file_path in sorted(versions_dir.glob("*.py")):
        content = file_path.read_text(encoding="utf-8")
        match = REVISION_PATTERN.search(content)
        if not match:
            raise ValueError(f"Missing revision declaration in {file_path}")
        files.append((file_path, match.group("revision")))
    return files


def main() -> int:
    backend_root = _backend_root()
    versions_dir = _versions_dir(backend_root)
    problems: list[str] = []

    if not versions_dir.exists():
        problems.append(f"Versions directory not found: {versions_dir}")
    else:
        try:
            revision_files = _collect_revision_files(versions_dir)
        except ValueError as exc:
            problems.append(str(exc))
        else:
            for file_path, revision in revision_files:
                if len(revision) > MAX_REVISION_LENGTH:
                    problems.append(
                        f"Revision ID too long in {file_path}: '{revision}' ({len(revision)} characters, max {MAX_REVISION_LENGTH})"
                    )

    exit_code, stdout, stderr = _run_alembic_heads(backend_root)
    heads_output = [line.strip() for line in stdout.splitlines() if line.strip()]

    if exit_code != 0:
        problems.append("alembic heads failed:")
        if stdout.strip():
            problems.append(stdout.strip())
        if stderr.strip():
            problems.append(stderr.strip())
    elif len(heads_output) != 1:
        problems.append(f"Expected exactly 1 Alembic head, found {len(heads_output)}.")
        if heads_output:
            problems.append("alembic heads output:")
            problems.extend(f"  {line}" for line in heads_output)
    elif heads_output:
        print(f"Alembic head: {heads_output[0]}")

    if problems:
        print("Alembic integrity check failed.", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1

    version_count = len(list(versions_dir.glob("*.py")))
    print(f"Alembic integrity check passed: 1 head, {version_count} revision file(s) validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
