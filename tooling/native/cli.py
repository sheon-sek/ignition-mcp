"""Command-line entry point for Runtime Bundle source validation/build."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import zipfile

from .archive import build_project, pending_native_bindings
from .project import ValidationError, validate_project


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--project-dir", required=True, type=Path)
    build = subparsers.add_parser("build")
    build.add_argument("--project-dir", required=True, type=Path)
    build.add_argument("--output", required=True, type=Path)
    build.add_argument("--allow-unbound-scaffold", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.command == "validate":
            files = validate_project(args.project_dir)
        else:
            files = build_project(args.project_dir, args.output, allow_unbound_scaffold=args.allow_unbound_scaffold)
    except (OSError, ValidationError, zipfile.BadZipFile) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    pending = pending_native_bindings(files)
    print(f"{args.command}: OK ({len(files)} files)")
    print("nativeResponseBindingStatus=" + ("NATIVE_BINDING_PENDING" if pending else "UNVERIFIED"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
