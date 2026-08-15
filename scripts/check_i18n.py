#!/usr/bin/env python
"""Audit i18n completeness against en.json.

Run from the repo root:

    python scripts/check_i18n.py

Exits non-zero if any non-English locale has missing OR extra keys.

Extras count as a failure because of how a key gets renamed: the new name is
added to all five files and the old one is deleted from the one being worked
on. The other four keep a dead string that no code reads, and nothing says so.
A locale that has drifted in either direction has drifted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def flat_keys(d: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out |= flat_keys(v, full)
        else:
            out.add(full)
    return out


def main() -> int:
    i18n_dir = Path("web/i18n")
    base = json.loads((i18n_dir / "en.json").read_text(encoding="utf-8"))
    en_keys = flat_keys(base)
    print(f"Reference (en.json): {len(en_keys)} keys")

    failed = False
    for path in sorted(i18n_dir.glob("*.json")):
        if path.name == "en.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        keys = flat_keys(data)
        missing = sorted(en_keys - keys)
        extra = sorted(keys - en_keys)
        status = "OK" if not missing and not extra else "DRIFTED"
        print(
            f"{path.stem:6s} -> {len(keys):3d} keys, "
            f"missing {len(missing):3d}, extra {len(extra):3d}  [{status}]"
        )
        if missing:
            failed = True
            for k in missing[:25]:
                print(f"   - {k}")
            if len(missing) > 25:
                print(f"   ... ({len(missing) - 25} more)")
        if extra:
            failed = True
            for k in extra[:25]:
                print(f"   + {k}  (not in en.json: renamed or left behind)")
            if len(extra) > 25:
                print(f"   ... ({len(extra) - 25} more)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
