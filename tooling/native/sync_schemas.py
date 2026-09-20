"""Publish self-contained source schemas as deterministic Designer Text Resources."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    for name in ("bundle-info", "tag-browse", "tag-read"):
        payload = (ROOT / f"contracts/schemas/{name}.output.schema.json").read_bytes()
        target = ROOT / "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/resources/contracts" / f"{name}-output"
        metadata_path = target / "resource.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["attributes"]["size"] = len(payload)
        (target / "data.bin").write_bytes(payload)
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    main()
