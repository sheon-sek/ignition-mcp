#!/usr/bin/env python3
"""Wait until the disposable Gateway answers Native REST and the MCP endpoint.

Both readiness points the workflow needs — after the fixture install and after
the Gateway restart — are the same two checks, so they live here instead of in
two shell loops that can drift apart. The origin is never contacted outside the
one the driver accepts (`127.0.0.1:8093`); the URL check stays in `driver.py`, and
this waiter is only ever given that URL by the workflow.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gateway_rest  # noqa: E402
import mcp_client  # noqa: E402

EXIT_READY = 0
EXIT_NOT_READY = 2


def rest_ready(base_url: str, api_token: str) -> tuple[bool, str, dict | None]:
    try:
        status, payload = gateway_rest.request(
            base_url, api_token, "GET", "/data/api/v1/gateway-info", timeout=5.0,
        )
    except gateway_rest.RestError as error:
        return False, str(error), None
    if status != 200:
        return False, f"gateway-info returned HTTP {status}", None
    document = gateway_rest.decode(payload)
    if not isinstance(document, dict):
        return False, "gateway-info was not an object", None
    return True, "", document


def mcp_ready(mcp_url: str, api_token: str) -> tuple[bool, str]:
    client = mcp_client.McpClient(mcp_url, api_token, timeout=15.0)
    try:
        result = client.initialize()
    except mcp_client.McpError as error:
        return False, str(error)
    if "protocolVersion" not in result:
        return False, "the MCP initialize result carried no protocolVersion"
    return True, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wait for the disposable Gateway's REST and MCP surfaces")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-token", required=True)
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--evidence-dir")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    deadline = time.monotonic() + args.timeout
    rest_ok = False
    mcp_ok = False
    last_error = ""
    while time.monotonic() < deadline:
        if not rest_ok:
            rest_ok, last_error, info = rest_ready(args.base_url, args.api_token)
            if rest_ok and args.evidence_dir and info is not None:
                path = Path(args.evidence_dir)
                path.mkdir(parents=True, exist_ok=True)
                (path / "gateway-info.json").write_text(
                    json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8",
                )
        if not mcp_ok:
            mcp_ok, last_error = mcp_ready(args.mcp_url, args.api_token)
        if rest_ok and mcp_ok:
            print(json.dumps({"rest": True, "mcp": True}))
            return EXIT_READY
        time.sleep(3.0)
    print(json.dumps({"rest": rest_ok, "mcp": mcp_ok, "lastError": last_error}), file=sys.stderr)
    return EXIT_NOT_READY


if __name__ == "__main__":
    raise SystemExit(main())
