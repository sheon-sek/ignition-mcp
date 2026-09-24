"""Publish self-contained source schemas as deterministic Designer Text Resources."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from .validation import CRLF_LINE_ENDING_HINT, ValidationError, require

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_NAMES = ("bundle-info", "tag-browse", "tag-read")


def sync_schemas(root: Path = ROOT) -> None:
    """Copy each source schema into its Text Resource, refusing CRLF sources.

    Every source is read and checked before any target is written, so a CRLF checkout
    leaves the bundle tree untouched instead of committing a mixed line-ending state.
    """
    sources: list[tuple[str, bytes]] = []
    for name in SCHEMA_NAMES:
        relative = f"contracts/schemas/{name}.output.schema.json"
        payload = (root / relative).read_bytes()
        require(b"\r" not in payload, relative, CRLF_LINE_ENDING_HINT)
        sources.append((name, payload))

    for name, payload in sources:
        target = root / "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/resources/contracts" / f"{name}-output"
        metadata_path = target / "resource.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["attributes"]["size"] = len(payload)
        (target / "data.bin").write_bytes(payload)
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8", newline="\n")


def main(root: Path = ROOT) -> int:
    try:
        sync_schemas(root)
    except (OSError, ValidationError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
