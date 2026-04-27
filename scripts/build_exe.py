"""Build the standalone Windows bundle.

Usage:
    python scripts/build_exe.py

Produces:
    dist/JobFinder/                    # PyInstaller --onedir output
    dist/JobFinder-windows.zip         # zipped distributable
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "JobFinder.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def main() -> int:
    if not SPEC.exists():
        print(f"missing spec: {SPEC}", file=sys.stderr)
        return 1
    for path in (DIST, BUILD):
        if path.exists():
            shutil.rmtree(path)
    print("running PyInstaller...")
    subprocess.check_call(
        ["pyinstaller", str(SPEC), "--noconfirm"],
        cwd=ROOT,
    )
    bundle = DIST / "JobFinder"
    if not bundle.exists():
        print("PyInstaller did not produce dist/JobFinder/", file=sys.stderr)
        return 2
    zip_path = DIST / "JobFinder-windows.zip"
    print(f"creating {zip_path.name}...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in bundle.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(bundle.parent))
    size_mb = zip_path.stat().st_size // (1024 * 1024)
    print(f"done: {zip_path} ({size_mb} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
