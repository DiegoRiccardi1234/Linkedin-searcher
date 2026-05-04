"""File-sync primitive used by the standalone updater.

Copies every file from a freshly-extracted bundle directory into the
install directory, skipping a small whitelist of user-owned paths
(``data/``, ``.env``, ``.env.local``). The point is that the SQLite
DB, uploaded CV, API keys, and any environment overrides survive an
update untouched.

This module is import-clean (no side effects) so the unit tests can
exercise ``sync_install_dir`` without going through the rest of the
binary.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

# Top-level paths in the install dir that the updater must NEVER overwrite
# or delete. Match by path component, so ``data/searcher.db`` is preserved
# because ``data`` is in this set.
PRESERVE_TOPLEVEL = ("data", ".env", ".env.local")


def _is_preserved(rel_path: Path) -> bool:
    parts = rel_path.parts
    return bool(parts) and parts[0] in PRESERVE_TOPLEVEL


_COPY_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0, 16.0)


def _copy_with_retry(src: Path, dst: Path) -> None:
    """Copy ``src`` to ``dst`` with retries on PermissionError.

    Windows holds file locks via antivirus scans (Defender on a fresh
    extract can take 10-20 s) or just-exited processes whose handles
    are still draining. Without retries, the updater aborts on the
    first transient lock and leaves the install dir half-updated.
    Retry up to 5 times with 1s/2s/4s/8s/16s backoff (~31 s total)
    so the final error message includes the specific file that stayed
    locked, which is invaluable for diagnosing antivirus interference.
    """
    for delay in _COPY_RETRY_DELAYS:
        try:
            shutil.copy2(src, dst)
            return
        except PermissionError:
            time.sleep(delay)
    try:
        shutil.copy2(src, dst)
    except PermissionError as exc:
        # Augment the error with the specific destination path so logs
        # tell us which file stayed locked across all retries (typically
        # JobFinder.exe itself or a recent child it spawned).
        raise PermissionError(
            exc.errno,
            f"{exc.strerror} (locked after {len(_COPY_RETRY_DELAYS)} retries): {dst}",
        ) from exc


def sync_install_dir(*, source: Path, target: Path) -> int:
    """Copy everything under ``source`` into ``target``.

    Returns the number of files written. Files matching
    ``PRESERVE_TOPLEVEL`` are skipped on the source side so user data
    that happens to ship inside the bundle (it shouldn't, but defensive)
    can never clobber the user's real data.
    """
    if not source.exists():
        raise FileNotFoundError(f"source bundle not found: {source}")
    target.mkdir(parents=True, exist_ok=True)

    written = 0
    for src in source.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(source)
        if _is_preserved(rel):
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        _copy_with_retry(src, dst)
        written += 1
    return written
