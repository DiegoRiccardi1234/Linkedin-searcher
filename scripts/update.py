#!/usr/bin/env python
"""Update Job Finder to the latest GitHub release.

Runs `git pull` then `pip install -r requirements.txt` against the
current Python interpreter. Returns combined output. Intended to be
invoked from the in-app banner ("Update now") via /api/update.

Manual usage:

    python scripts/update.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=180,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


def update(repo_root: Path | None = None) -> dict:
    root = repo_root or Path(__file__).resolve().parents[1]

    steps: list[dict] = []

    code, out = _run(["git", "fetch", "--all", "--prune"], root)
    steps.append({"step": "git fetch", "code": code, "output": out})
    if code != 0:
        return {"ok": False, "steps": steps, "message": "git fetch failed"}

    code, out = _run(["git", "pull", "--ff-only"], root)
    steps.append({"step": "git pull", "code": code, "output": out})
    if code != 0:
        return {
            "ok": False,
            "steps": steps,
            "message": "git pull failed (uncommitted changes or non fast-forward?)",
        }

    code, out = _run(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--quiet"],
        root,
    )
    steps.append({"step": "pip install", "code": code, "output": out})
    if code != 0:
        return {"ok": False, "steps": steps, "message": "pip install failed"}

    return {
        "ok": True,
        "steps": steps,
        "message": "Update applied. Restart the app to load the new version.",
    }


if __name__ == "__main__":
    result = update()
    print(result["message"])
    for s in result["steps"]:
        print(f"\n=== {s['step']} (exit {s['code']}) ===")
        print(s["output"])
    sys.exit(0 if result["ok"] else 1)
