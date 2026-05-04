"""Standalone updater for JobFinder.exe.

Bundled by PyInstaller alongside JobFinder.exe as ``Updater.exe``.
Invoked by the running JobFinder process when the user clicks
"Update now"; the JobFinder process then exits so its files are
unlocked and we can copy the new bundle on top.

CLI:
    Updater --install-dir <dir> --parent-pid <pid> [--zip <path>]

Behavior:
    1. Wait for the parent JobFinder PID to die (so its files unlock).
    2. Download the latest GitHub release ZIP, OR use --zip if given.
    3. Extract into a temp dir.
    4. ``app.update_sync.sync_install_dir`` copies bundle files over
       the install dir, preserving ``data/``, ``.env``, ``.env.local``.
    5. Restart JobFinder.exe.
    6. On any failure, leave the install dir intact — the user can
       still launch the old version.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

if getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(sys.executable).resolve().parent / "_internal"))

from app.update_sync import sync_install_dir
from app.version import _fetch_latest_release


def _wait_for_pid(pid: int, timeout: float = 30) -> None:
    """Block until the given PID is no longer running, or timeout."""
    end = time.time() + timeout
    while time.time() < end:
        if sys.platform == "win32":
            kernel = ctypes.windll.kernel32
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return
            still_active = 259
            code = ctypes.c_ulong()
            ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            kernel.CloseHandle(handle)
            if ok and code.value != still_active:
                return
        else:
            try:
                os.kill(pid, 0)
            except OSError:
                return
        time.sleep(0.3)


def _download_latest(target_dir: Path) -> Path:
    release = _fetch_latest_release()
    if not release:
        raise RuntimeError("could not fetch latest release info from GitHub")
    asset_url: str | None = None
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.endswith("windows.zip"):
            asset_url = asset.get("browser_download_url")
            break
    if not asset_url:
        raise RuntimeError("no '*windows.zip' asset in latest release")
    target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / "JobFinder-windows.zip"
    with urllib.request.urlopen(asset_url, timeout=120) as r, open(zip_path, "wb") as f:
        shutil.copyfileobj(r, f)
    return zip_path


def _open_log(install_dir: Path):
    log_path = install_dir / "data" / "logs" / "updater.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path.open("a", encoding="utf-8")


def _show_misuse_dialog() -> None:
    """When user double-clicks Updater.exe, show a friendly dialog instead of a
    silent argparse crash. The updater is an internal helper; it must be
    spawned by JobFinder so the parent PID and install dir are known and the
    parent can release its file locks before files get overwritten."""
    if sys.platform != "win32":
        return
    title = "JobFinder Updater"
    text = (
        "Updater.exe is launched automatically by JobFinder.\n\n"
        "Open JobFinder.exe and click 'Update now' from the update banner."
    )
    with contextlib.suppress(Exception):
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x00000040)


def main() -> int:
    if len(sys.argv) <= 1:
        _show_misuse_dialog()
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-dir", required=True, type=Path)
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument(
        "--zip",
        type=Path,
        default=None,
        help="Use a pre-downloaded ZIP instead of fetching the latest release.",
    )
    args = parser.parse_args()

    install_dir = args.install_dir.resolve()
    log_file = _open_log(install_dir)

    def log(msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {msg}"
        print(line)
        log_file.write(line + "\n")
        log_file.flush()

    def event(name: str, **fields: Any) -> None:
        payload = {"event": name, "ts": time.time(), **fields}
        log_file.write("EVENT " + json.dumps(payload) + "\n")
        log_file.flush()

    with contextlib.closing(log_file):
        try:
            event("started", parent_pid=args.parent_pid)
            log(f"updater starting; waiting for parent PID {args.parent_pid}")
            _wait_for_pid(args.parent_pid)
            log("parent exited, proceeding")
            event("parent_exited")
            # Grace period: Windows can take a few seconds after process
            # exit to flush all inherited handles (uvicorn workers, OCR
            # subprocesses, AV pre-scan handles). Sync starting too eagerly
            # races with these and produces PermissionError on the first
            # file. 3 s is empirically enough on consumer hardware.
            time.sleep(3.0)

            with tempfile.TemporaryDirectory(prefix="jobfinder-update-") as td:
                tmp = Path(td)
                if args.zip:
                    zip_path = args.zip.resolve()
                    log(f"using pre-downloaded ZIP: {zip_path}")
                    event("download_skipped", zip=str(zip_path))
                else:
                    log("downloading latest release ZIP from GitHub")
                    event("download_start")
                    zip_path = _download_latest(tmp)
                    log(f"downloaded to {zip_path}")
                    event("download_done", bytes=zip_path.stat().st_size)

                extract_dir = tmp / "extracted"
                extract_dir.mkdir()
                event("verify_start")
                with zipfile.ZipFile(zip_path) as z:
                    z.extractall(extract_dir)
                log(f"extracted into {extract_dir}")
                event("verify_done")

                # The ZIP root is the `JobFinder/` folder produced by PyInstaller.
                inner_dirs = [p for p in extract_dir.iterdir() if p.is_dir()]
                source = inner_dirs[0] if len(inner_dirs) == 1 else extract_dir

                event("replace_start")
                count = sync_install_dir(source=source, target=install_dir)
                log(f"sync done: {count} files written, data/ preserved")
                event("replace_done", files=count)

            exe = install_dir / "JobFinder.exe"
            log(f"restarting {exe}")
            event("restart_spawned", exe=str(exe))
            # Detach the new JobFinder.exe so it survives Updater.exe exit.
            # Without DETACHED_PROCESS the child inherits Updater's console;
            # when Updater returns the console closes and the just-spawned
            # JobFinder dies with it, leaving the user with no app and a
            # frontend modal stuck at "Restart 95%" forever.
            restart_flags = 0
            if sys.platform == "win32":
                restart_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(
                [str(exe)],
                cwd=str(install_dir),
                close_fds=True,
                creationflags=restart_flags,
            )
            # Best-effort lockfile cleanup so the next update isn't blocked
            # by a stale lock. The backend TTL covers crash cases.
            with contextlib.suppress(OSError):
                (install_dir / "data" / "update.lock").unlink(missing_ok=True)
            return 0
        except Exception as exc:
            log(f"FAILED: {exc!r}")
            event("error", message=repr(exc))
            with contextlib.suppress(OSError):
                (install_dir / "data" / "update.lock").unlink(missing_ok=True)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
