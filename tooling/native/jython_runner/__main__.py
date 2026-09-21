"""Command-line entry point for the recorded Jython runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .runner import run_recorded_tool


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", help="Runtime Tool name, for example tag_query")
    parser.add_argument(
        "fixture",
        type=Path,
        help="recorded system.* fixture JSON from tooling/native/jython_runner/fixtures",
    )
    arguments = parser.parse_args()
    print(json.dumps(run_recorded_tool(arguments.tool, arguments.fixture), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
