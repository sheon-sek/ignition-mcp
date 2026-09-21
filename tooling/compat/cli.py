"""CLI: python -m tooling.compat validate --evidence-dir tests/compatibility/evidence"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from tooling.compat.evidence import EvidenceError, load_evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tooling.compat")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--evidence-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        rows = load_evidence(args.evidence_dir)
    except EvidenceError as error:
        print(f"evidence: REJECTED: {error}", file=sys.stderr)
        return 1
    print(f"evidence: OK ({len(rows)} rows, no SUPPORTED claim)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
