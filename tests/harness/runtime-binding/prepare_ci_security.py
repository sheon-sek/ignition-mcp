#!/usr/bin/env python3
"""Prepare deterministic CI-only Ignition API-token security resources.

This operates on copies of a freshly commissioned Gateway's resource files so
unrelated Gateway security settings remain intact.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Any, cast

TOKEN_NAME = "ignition-mcp-ci"
SECURITY_LEVEL = "IgnitionMcpCi"
SECURITY_LEVEL_DESCRIPTION = "Disposable CI-only full Gateway API access for ignition-mcp tests."
AUTHENTICATED_DESCRIPTION = "Represents a user who has been authenticated by the system."
TOKEN_KEY = "zG48znDwfapnZCJA_d7THMrQJpejwONfXMFZ5oBYn0I"
TOKEN_RESOURCE_UUID = "36c19c80-f874-45bb-aabc-4e22b81bd89c"
TOKEN_TIMESTAMP = 1789900000000


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return cast(dict[str, Any], value)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _decode_key() -> bytes:
    padding = "=" * (-len(TOKEN_KEY) % 4)
    raw = base64.urlsafe_b64decode(TOKEN_KEY + padding)
    if len(raw) != 32:
        raise ValueError("CI token key must decode to exactly 32 bytes")
    return raw


def _token_hash() -> str:
    # Ignition generates a 32-byte key, represents it as unpadded Base64URL,
    # and stores a SHA-256 digest of the decoded key bytes as unpadded Base64URL.
    digest = hashlib.sha256(_decode_key()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _permission_path() -> list[dict[str, Any]]:
    return [
        {
            "children": [{"children": [], "name": SECURITY_LEVEL}],
            "name": "Authenticated",
        }
    ]


def _token_security_levels() -> list[dict[str, Any]]:
    return [
        {
            "children": [
                {
                    "children": [],
                    "description": SECURITY_LEVEL_DESCRIPTION,
                    "name": SECURITY_LEVEL,
                }
            ],
            "description": AUTHENTICATED_DESCRIPTION,
            "name": "Authenticated",
        }
    ]


def _patch_security_levels(path: Path) -> None:
    config = _load(path)
    levels = config.get("securityLevels")
    if not isinstance(levels, list):
        raise ValueError(f"{path}: securityLevels must be an array")
    authenticated = next(
        (item for item in levels if isinstance(item, dict) and item.get("name") == "Authenticated"),
        None,
    )
    if not isinstance(authenticated, dict):
        raise ValueError(f"{path}: Authenticated security level is missing")
    children = authenticated.get("children")
    if not isinstance(children, list):
        raise ValueError(f"{path}: Authenticated.children must be an array")
    existing = [item for item in children if isinstance(item, dict) and item.get("name") == SECURITY_LEVEL]
    if len(existing) > 1:
        raise ValueError(f"{path}: duplicate {SECURITY_LEVEL} security levels")
    desired = {
        "children": [],
        "description": SECURITY_LEVEL_DESCRIPTION,
        "name": SECURITY_LEVEL,
    }
    if existing:
        existing[0].clear()
        existing[0].update(desired)
    else:
        children.append(desired)
    _write(path, config)


def _patch_security_properties(path: Path) -> None:
    config = _load(path)
    permission = {"securityLevels": _permission_path(), "type": "AnyOf"}
    for field in ("accessPermissions", "readPermissions", "writePermissions"):
        config[field] = permission
    _write(path, config)


def _write_api_token(root: Path) -> None:
    token_dir = root / "api-token" / TOKEN_NAME
    config = {
        "profile": {
            "secureChannelRequired": False,
            "securityLevels": _token_security_levels(),
            "timestamp": TOKEN_TIMESTAMP,
            "type": "basic-token",
        },
        "settings": {"tokenHash": _token_hash()},
    }
    resource = {
        "scope": "A",
        "description": "Disposable CI-only API token. Never use this credential outside ephemeral CI.",
        "version": 1,
        "restricted": False,
        "overridable": True,
        "files": ["config.json"],
        "attributes": {
            "uuid": TOKEN_RESOURCE_UUID,
            "enabled": True,
        },
    }
    _write(token_dir / "config.json", config)
    _write(token_dir / "resource.json", resource)


def prepare(root: Path, evidence: Path | None, github_env: Path | None) -> None:
    _patch_security_levels(root / "security-levels" / "config.json")
    _patch_security_properties(root / "security-properties" / "config.json")
    _write_api_token(root)

    if evidence is not None:
        _write(
            evidence,
            {
                "schemaVersion": 1,
                "tokenName": TOKEN_NAME,
                "tokenHash": _token_hash(),
                "secureChannelRequired": False,
                "securityLevelPath": f"Authenticated/{SECURITY_LEVEL}",
                "gatewayPermissions": ["access", "read", "write"],
                "credentialScope": "ephemeral-ci-only",
            },
        )
    if github_env is not None:
        with github_env.open("a", encoding="utf-8") as handle:
            handle.write(f"CI_API_TOKEN={TOKEN_NAME}:{TOKEN_KEY}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--github-env", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    prepare(args.root, args.evidence, args.github_env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
