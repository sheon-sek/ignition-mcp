"""CLI: python -m tooling.compat validate --evidence-dir tests/compatibility/evidence"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from tooling.compat.evidence import EvidenceError, load_evidence
from tooling.compat.g4 import G4Error, generate_g4
from tooling.compat.g5 import G5Error, generate_g5
from tooling.compat.generate import GenerateError, generate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tooling.compat")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--evidence-dir", required=True, type=Path)
    gen = subparsers.add_parser("generate")
    gen.add_argument("--observations", required=True, type=Path, help="driver observations.json")
    gen.add_argument("--identity", required=True, type=Path, help="run identity material JSON")
    gen.add_argument("--gate-off-report", type=Path, default=None,
                     help="inventory_gate_off.py report to embed")
    gen.add_argument("--out-dir", required=True, type=Path, help="fresh directory to write g3-<...>/ into")
    g4 = subparsers.add_parser("g4")
    g4.add_argument("--close", required=True, type=Path, help="the G4 close document (authored input)")
    g4.add_argument("--artifacts", required=True, type=Path,
                    help="directory holding `run-<id>/` artifact downloads the cited runs came from")
    g4.add_argument("--out-dir", required=True, type=Path, help="fresh directory to write g4-<...>/ into")
    g5 = subparsers.add_parser("g5")
    g5.add_argument("--close", required=True, type=Path, help="the G5 close document (authored input)")
    g5.add_argument("--stage", required=True, type=Path,
                    help="the class-enabled pass's observations.json, downloaded from the run")
    g5.add_argument("--identity", required=True, type=Path, help="the run's identity.json")
    g5.add_argument("--evidence-dir", required=True, type=Path,
                    help="the evidence tree the cited rows are resolved in")
    g5.add_argument("--run-id", required=True, help="the workflow run id that produced the stage")
    g5.add_argument("--workflow", required=True, help="the workflow name that produced the stage")
    g5.add_argument("--head", required=True, help="the commit the run checked out")
    g5.add_argument("--conclusion", default="success", help="the run's conclusion (a green run only)")
    g5.add_argument("--out-dir", required=True, type=Path, help="fresh directory to write g5-<...>/ into")
    args = parser.parse_args()

    if args.command == "g5":
        try:
            directory = generate_g5(
                args.close, args.stage, args.identity, args.out_dir,
                evidence_root=args.evidence_dir,
                run={
                    "runId": args.run_id, "workflow": args.workflow,
                    "head": args.head, "conclusion": args.conclusion,
                },
            )
        except (G5Error, EvidenceError) as error:
            print(f"g5: REJECTED: {error}", file=sys.stderr)
            return 1
        print(f"g5: OK ({directory})")
        return 0

    if args.command == "g4":
        try:
            directory = generate_g4(args.close, args.out_dir, args.artifacts)
        except (G4Error, EvidenceError) as error:
            print(f"g4: REJECTED: {error}", file=sys.stderr)
            return 1
        print(f"g4: OK ({directory})")
        return 0

    if args.command == "generate":
        try:
            directory = generate(args.observations, args.out_dir, args.identity, args.gate_off_report)
        except (GenerateError, EvidenceError) as error:
            print(f"generate: REJECTED: {error}", file=sys.stderr)
            return 1
        print(f"generate: OK ({directory})")
        return 0

    try:
        rows = load_evidence(args.evidence_dir)
    except EvidenceError as error:
        print(f"evidence: REJECTED: {error}", file=sys.stderr)
        return 1
    print(f"evidence: OK ({len(rows)} rows, no SUPPORTED claim)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
