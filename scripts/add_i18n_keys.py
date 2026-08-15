#!/usr/bin/env python
"""Add i18n keys to all five locale files from one patch file.

Run from the repo root:

    python scripts/add_i18n_keys.py path/to/patch.json

The patch is shaped like the locale files themselves, one top-level object per
language::

    {
      "en": {"jobs": {"shownOf": "showing {shown} of {total}"}},
      "it": {"jobs": {"shownOf": "mostrate {shown} di {total}"}},
      ...
    }

Every language present in ``web/i18n`` must appear in the patch: a key added to
four locales out of five is exactly the failure ``check_i18n.py`` exists to
catch, and catching it here is cheaper than catching it in the gate.

New keys are merged into the object that already holds their siblings, so they
land next to the strings they belong with instead of at the end of the file. An
existing value is only overwritten with ``--overwrite``, because silently
rewriting a translation someone edited by hand is not a merge.

Why a script at all: editing five JSON files by hand loses an accent, drops a
comma, or writes ``\\u00e8`` where the rest of the file has ``è``. The writer
below always uses ``ensure_ascii=False``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

I18N_DIR = Path("web/i18n")


def flat_keys(d: dict[str, Any], prefix: str = "") -> set[str]:
    out: set[str] = set()
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out |= flat_keys(v, full)
        else:
            out.add(full)
    return out


def deep_merge(
    target: dict[str, Any],
    patch: dict[str, Any],
    *,
    overwrite: bool,
    prefix: str = "",
) -> tuple[list[str], list[str]]:
    """Merge ``patch`` into ``target`` in place. Returns (added, skipped)."""
    added: list[str] = []
    skipped: list[str] = []
    for key, value in patch.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            existing = target.get(key)
            if not isinstance(existing, dict):
                if key in target and not overwrite:
                    skipped.append(full)
                    continue
                target[key] = {}
            sub_added, sub_skipped = deep_merge(
                target[key], value, overwrite=overwrite, prefix=full
            )
            added.extend(sub_added)
            skipped.extend(sub_skipped)
            continue
        if key in target and not overwrite:
            skipped.append(full)
            continue
        target[key] = value
        added.append(full)
    return added, skipped


def write_locale(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patch", type=Path, help="JSON file: {lang: {nested keys}}")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace values that already exist (default: leave them alone)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print what would change")
    args = parser.parse_args()

    patch = json.loads(args.patch.read_text(encoding="utf-8"))
    if not isinstance(patch, dict):
        print("Patch must be an object keyed by language.", file=sys.stderr)
        return 2

    locales = sorted(p.stem for p in I18N_DIR.glob("*.json"))
    missing_langs = [lang for lang in locales if lang not in patch]
    if missing_langs:
        print(
            f"Patch is missing these languages: {', '.join(missing_langs)}. "
            "Add them (a translation, not a copy of the English) and run again.",
            file=sys.stderr,
        )
        return 2

    failed = False
    for lang in locales:
        path = I18N_DIR / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        before = flat_keys(data)
        added, skipped = deep_merge(data, patch[lang], overwrite=args.overwrite)
        after = flat_keys(data)
        # A merge only ever adds. If a key vanished, the patch shadowed an
        # object with a string (or the reverse) and the file is now wrong.
        lost = sorted(before - after)
        if lost:
            failed = True
            print(f"{lang}: refusing to write, these keys would disappear: {lost}", file=sys.stderr)
            continue
        if not args.dry_run:
            write_locale(path, data)
        note = " (dry run)" if args.dry_run else ""
        print(f"{lang:3s} -> +{len(added)} added, {len(skipped)} left alone{note}")
        for key in skipped:
            print(f"    kept existing: {key}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
