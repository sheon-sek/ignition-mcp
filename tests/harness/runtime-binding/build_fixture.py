#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tooling.native.archive import build_project  # noqa: E402


def main() -> int:
    project = Path(__file__).resolve().parent / "project"
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "phase0-characterization.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    build_project(project, output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
