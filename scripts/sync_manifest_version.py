#!/usr/bin/env python3
"""Sync custom_components/studylife/manifest.json's version field to a released version.

Usage: sync_manifest_version.py <version>
Called by semantic-release's @semantic-release/exec prepare step so the manifest's
user-facing version (shown in Home Assistant's integration info page) never drifts
from the actual GitHub release tag.
"""
import json
import re
import sys

MANIFEST_PATH = "custom_components/studylife/manifest.json"


def main() -> None:
    version = sys.argv[1]
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        original = f.read()

    # Confirm "version" is actually a top-level key before touching anything.
    json.loads(original)["version"]

    # Regex substitution instead of json.dump - preserves the file's existing formatting
    # (compact arrays, key order) instead of reformatting the whole file on every release.
    updated, count = re.subn(
        r'("version"\s*:\s*)"[^"]*"', rf'\g<1>"{version}"', original, count=1
    )
    if count != 1:
        raise SystemExit(f'could not find a "version" field to update in {MANIFEST_PATH}')

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        f.write(updated)


if __name__ == "__main__":
    main()
