"""
version_manager.py — V4.1

Auto-versioning and GitHub automation for MNQ AI Trader.

Responsibilities:
  - Read/write VERSION in .env (single source of truth)
  - Bump patch/minor/major version
  - Auto-commit changed files to git with generated message
  - Push to origin main
  - Tag releases on minor/major bumps
  - Generate changelog entry from git diff summary

Called by:
  - learning_session.py at EOD (auto-commit + patch bump)
  - Manually: py -3.11 version_manager.py --bump minor --message "Add regime detection"
"""

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader"))
ENV_FILE = BASE_DIR / ".env"


# ── Version helpers ──────────────────────────────────────────

def read_version() -> str:
    """Read current version from .env BOT_VERSION field."""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("BOT_VERSION="):
                return line.split("=", 1)[1].strip()
    return "4.1.0"


def write_version(version: str) -> None:
    """Write BOT_VERSION to .env. Adds it if not present."""
    if not ENV_FILE.exists():
        return
    content = ENV_FILE.read_text(encoding="utf-8")
    if "BOT_VERSION=" in content:
        content = re.sub(r"^BOT_VERSION=.*$", f"BOT_VERSION={version}",
                         content, flags=re.MULTILINE)
    else:
        content += f"\nBOT_VERSION={version}\n"
    ENV_FILE.write_text(content, encoding="utf-8")


def bump_version(current: str, level: str = "patch") -> str:
    """
    Bump version string.
    level: "patch" (4.1.0→4.1.1) | "minor" (4.1.0→4.2.0) | "major" (4.1.0→5.0.0)
    """
    parts = current.split(".")
    if len(parts) != 3:
        parts = ["4", "1", "0"]
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    if level == "major":
        return f"{major + 1}.0.0"
    elif level == "minor":
        return f"{major}.{minor + 1}.0"
    else:
        return f"{major}.{minor}.{patch + 1}"


# ── Git helpers ──────────────────────────────────────────────

def _run_git(args: list[str], cwd: Path = BASE_DIR) -> tuple[int, str, str]:
    """Run a git command. Returns (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def get_changed_files() -> list[str]:
    """Return list of files changed vs HEAD."""
    rc, out, _ = _run_git(["diff", "--name-only", "HEAD"])
    if rc != 0:
        # No HEAD yet (first commit)
        rc, out, _ = _run_git(["status", "--short"])
        return [line[3:].strip() for line in out.splitlines() if line.strip()]
    return [f for f in out.splitlines() if f.strip()]


def get_diff_summary() -> str:
    """Get a short summary of what changed for the changelog."""
    rc, out, _ = _run_git(["diff", "--stat", "HEAD"])
    if rc != 0 or not out:
        return "Various code changes"
    # Just the last line (summary line)
    lines = [l for l in out.splitlines() if l.strip()]
    return lines[-1] if lines else "Various code changes"


def git_add_all() -> bool:
    """Stage all tracked + new non-ignored files."""
    rc, _, err = _run_git(["add", "-A"])
    return rc == 0


def git_commit(message: str) -> bool:
    """Commit staged changes."""
    rc, out, err = _run_git(["commit", "-m", message])
    if rc != 0 and "nothing to commit" in (out + err):
        return True   # Nothing to commit is fine
    return rc == 0


def git_push() -> bool:
    """Push to origin main."""
    rc, _, err = _run_git(["push", "origin", "main"])
    return rc == 0


def git_tag(tag: str, message: str) -> bool:
    """Create an annotated tag."""
    rc, _, _ = _run_git(["tag", "-a", tag, "-m", message])
    if rc == 0:
        _run_git(["push", "origin", tag])
    return rc == 0


def git_log_since(n: int = 5) -> str:
    """Get last N commit messages."""
    rc, out, _ = _run_git(["log", f"--oneline", f"-{n}"])
    return out if rc == 0 else ""


# ── Main EOD auto-commit ─────────────────────────────────────

def eod_commit(
    session_summary: str = "",
    bump: str = "patch",
    extra_message: str = "",
) -> str:
    """
    Called by learning_session.py at EOD.
    Bumps patch version, commits all changes, pushes to GitHub.
    Returns the new version string.
    """
    current  = read_version()
    new_ver  = bump_version(current, bump)
    date_str = datetime.now().strftime("%Y-%m-%d")

    # Write new version to .env
    write_version(new_ver)

    # Build commit message
    diff_summary = get_diff_summary()
    lines = [f"v{new_ver} — EOD auto-commit {date_str}"]
    if extra_message:
        lines.append(extra_message)
    if session_summary:
        lines.append(f"Session: {session_summary}")
    lines.append(f"Changes: {diff_summary}")
    commit_msg = "\n".join(lines)

    # Stage + commit + push
    if not git_add_all():
        print(f"[version_manager] git add failed")
        return new_ver

    if not git_commit(commit_msg):
        print(f"[version_manager] git commit failed (may be nothing to commit)")
    else:
        print(f"[version_manager] Committed v{new_ver}")

    if git_push():
        print(f"[version_manager] Pushed to GitHub")
    else:
        print(f"[version_manager] Push failed — check auth / network")

    # Tag on minor or major bumps
    if bump in ("minor", "major"):
        tag = f"v{new_ver}"
        if git_tag(tag, f"Release {tag}"):
            print(f"[version_manager] Tagged {tag}")

    return new_ver


# ── CLI ──────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="MNQ AI Trader version manager")
    parser.add_argument("--bump", choices=["patch", "minor", "major"],
                        default="patch", help="Version bump level")
    parser.add_argument("--message", default="", help="Commit message")
    parser.add_argument("--show", action="store_true", help="Show current version")
    parser.add_argument("--tag", action="store_true", help="Force tag this version")
    args = parser.parse_args()

    current = read_version()

    if args.show:
        print(f"Current version: {current}")
        print(f"Recent commits:\n{git_log_since(5)}")
        return

    new_ver = eod_commit(bump=args.bump, extra_message=args.message)
    print(f"Version: {current} → {new_ver}")

    if args.tag:
        git_tag(f"v{new_ver}", f"Manual release v{new_ver}")


if __name__ == "__main__":
    main()
