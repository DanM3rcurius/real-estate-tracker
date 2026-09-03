#!/usr/bin/env python3
"""Copy config/*.yaml into the package so an installed copy has real defaults.

Run after editing anything in config/. `tests/test_config_defaults.py` fails if
the two ever drift, so this cannot be forgotten silently.
"""

from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "config"
TARGET = REPO_ROOT / "src" / "hofradar" / "_config_defaults"


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    changed: list[str] = []
    for path in sorted(SOURCE.glob("*.yaml")):
        destination = TARGET / path.name
        if not destination.exists() or not filecmp.cmp(path, destination, shallow=False):
            shutil.copy2(path, destination)
            changed.append(path.name)
    for stale in sorted(TARGET.glob("*.yaml")):
        if not (SOURCE / stale.name).exists():
            stale.unlink()
            changed.append(f"{stale.name} (removed)")
    print("synced: " + (", ".join(changed) if changed else "nothing to do"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
