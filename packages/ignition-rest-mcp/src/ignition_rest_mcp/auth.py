"""Caller-facing trusted-internal authentication."""

from __future__ import annotations

import hmac

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token

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


class ExpiringJWTVerifier(JWTVerifier):
    """D07 requires expiry, whereas FastMCP permits tokens without exp."""

    async def load_access_token(self, token: str) -> AccessToken | None:
        verified = await super().load_access_token(token)
        if verified is None or verified.expires_at is None:
            return None
        return verified


def build_auth(settings: Settings) -> TokenVerifier | None:
    if settings.auth_mode == "jwt":
        return ExpiringJWTVerifier(
            public_key=settings.jwt_public_key,
            jwks_uri=settings.jwt_jwks_uri,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            algorithm="RS256",
            required_scopes=["ignition.read"],
        )
    if settings.auth_mode == "none":
        return None
    if settings.auth_mode == "static-token" and settings.static_token is not None:
        return ConstantTimeStaticTokenVerifier(settings.static_token)
    raise RuntimeError("validated auth configuration is inconsistent")


def operation_actor(settings: Settings) -> str:
    token = get_access_token()
    if token is not None:
        return token.subject or token.client_id
    return settings.service_identity
