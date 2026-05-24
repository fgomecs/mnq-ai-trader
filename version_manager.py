"""
version_manager.py — V4.1

Version tracking for MNQ AI Trader.

Responsibilities:
  - Read/write VERSION in .env (single source of truth)
  - Bump patch/minor/major version

Called by:
  - learning_session.py at EOD (patch bump)
  - Manually: py -3.11 version_manager.py --bump minor
"""

import os
import re
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


# ── EOD version bump ─────────────────────────────────────────

def eod_commit(
    session_summary: str = "",
    bump: str = "patch",
    extra_message: str = "",
) -> str:
    """
    Called by learning_session.py at EOD.
    Bumps version in .env and returns the new version string.
    Git operations are manual — this function does not touch git.
    """
    current = read_version()
    new_ver = bump_version(current, bump)

    write_version(new_ver)
    print(f"[version_manager] Version bumped: {current} → {new_ver}")

    return new_ver


# ── CLI ──────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="MNQ AI Trader version manager")
    parser.add_argument("--bump", choices=["patch", "minor", "major"],
                        default="patch", help="Version bump level")
    parser.add_argument("--message", default="", help="Commit message")
    parser.add_argument("--show", action="store_true", help="Show current version")
    args = parser.parse_args()

    current = read_version()

    if args.show:
        print(f"Current version: {current}")
        return

    new_ver = eod_commit(bump=args.bump, extra_message=args.message)
    print(f"Version: {current} → {new_ver}")


if __name__ == "__main__":
    main()
