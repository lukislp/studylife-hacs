#!/usr/bin/env python3
"""Turn a coverage.py JSON report into a shields.io "endpoint" badge JSON.

Usage: generate_coverage_badge.py <coverage.json> <out.json>
Reads totals.percent_covered (pytest --cov-report=json:coverage.json) and
writes it in the schema shields.io/endpoint expects, so the README badge
can point at the raw GitHub URL of the committed file - no external service.
"""
import json
import sys

THRESHOLDS = [(80, "brightgreen"), (60, "green"), (40, "yellow"), (20, "orange")]


def color_for(pct: float) -> str:
    for threshold, color in THRESHOLDS:
        if pct >= threshold:
            return color
    return "red"


def main() -> None:
    coverage_path, out_path = sys.argv[1], sys.argv[2]
    with open(coverage_path, encoding="utf-8") as f:
        pct = json.load(f)["totals"]["percent_covered"]

    badge = {
        "schemaVersion": 1,
        "label": "coverage",
        "message": f"{pct:.0f}%",
        "color": color_for(pct),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(badge, f)


if __name__ == "__main__":
    main()
