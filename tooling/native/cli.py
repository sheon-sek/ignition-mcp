"""Command-line entry point for Runtime Bundle source validation/build."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import zipfile

from .archive import build_project, pending_native_bindings
from .project import validate_project
from .release import release
from .validation import ValidationError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--project-dir", required=True, type=Path)
    build = subparsers.add_parser("build")
    build.add_argument("--project-dir", required=True, type=Path)
    build.add_argument("--output", required=True, type=Path)
    build.add_argument("--allow-unbound-scaffold", action="store_true")
    build.add_argument("--source-revision", default=None,
                       help="40-hex git SHA stamped into bundle_info; default UNSTAMPED")
    release_parser = subparsers.add_parser("release")
    release_parser.add_argument("--project-dir", required=True, type=Path)
    release_parser.add_argument("--out-dir", required=True, type=Path)
    release_parser.add_argument("--source-revision", required=True,
                                help="40-hex git SHA stamped into the release")
    release_parser.add_argument("--evidence-dir", required=True, type=Path,
                                help="compatibility evidence rows (read-only; never rewritten)")
    args = parser.parse_args(argv)

    try:
        if args.command == "release":
            paths = release(
                project_dir=args.project_dir, out_dir=args.out_dir,
                source_revision=args.source_revision, evidence_dir=args.evidence_dir,
            )
            for kind, path in paths.items():
                print(f"release {kind}: {path}")
            print("release: OK")
            return 0
        if args.command == "validate":
            files = validate_project(args.project_dir)
        else:
            files = build_project(
                args.project_dir, args.output,
                allow_unbound_scaffold=args.allow_unbound_scaffold,
                source_revision=args.source_revision,
            )
    except (OSError, ValidationError, zipfile.BadZipFile) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    pending = pending_native_bindings(files)
    print(f"{args.command}: OK ({len(files)} files)")
    print("nativeResponseBindingStatus=" + ("NATIVE_BINDING_PENDING" if pending else "UNVERIFIED"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
