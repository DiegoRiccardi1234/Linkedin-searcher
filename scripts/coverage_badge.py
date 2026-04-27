"""Generate a shields.io endpoint JSON from coverage.xml.

Reads `coverage.xml` (Cobertura) in the project root, computes the
overall line-rate percentage, and writes `coverage.json` in the
repository root in the format expected by the shields.io endpoint
badge URL.

Run after `pytest --cov=app --cov-report=xml`.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _color(percent: float) -> str:
    if percent >= 90:
        return "brightgreen"
    if percent >= 75:
        return "green"
    if percent >= 60:
        return "yellowgreen"
    if percent >= 40:
        return "yellow"
    if percent >= 20:
        return "orange"
    return "red"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    xml_path = root / "coverage.xml"
    if not xml_path.exists():
        print(f"coverage.xml not found at {xml_path}", file=sys.stderr)
        return 1

    tree = ET.parse(xml_path)
    line_rate = float(tree.getroot().get("line-rate", "0"))
    percent = round(line_rate * 100)

    payload = {
        "schemaVersion": 1,
        "label": "coverage",
        "message": f"{percent}%",
        "color": _color(percent),
    }
    out = root / "coverage.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}: {percent}% ({_color(percent)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
