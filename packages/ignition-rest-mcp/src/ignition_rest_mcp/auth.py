"""Caller-facing trusted-internal authentication."""

from __future__ import annotations

import hmac

from fastmcp.server.auth import AccessToken, TokenVerifier

from ignition_rest_mcp.config import Settings


class ConstantTimeStaticTokenVerifier(TokenVerifier):
    def __init__(self, token: str) -> None:
        super().__init__(required_scopes=["ignition.read"])
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self._token):
            return None
        return AccessToken(
            token=token,
            client_id="trusted-internal-static-token",
            scopes=["ignition.read"],
            subject="trusted-internal",
            claims={"deployment_profile": "trusted-internal"},
        )


def build_auth(settings: Settings) -> TokenVerifier | None:
    if settings.auth_mode == "none":
        return None
    if settings.auth_mode == "static-token" and settings.static_token is not None:
        return ConstantTimeStaticTokenVerifier(settings.static_token)
    raise RuntimeError("validated auth configuration is inconsistent")
