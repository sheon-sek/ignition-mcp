"""Caller authentication wiring and safe principal keys (D07/D07-A/D18)."""

from __future__ import annotations

from dataclasses import dataclass
import hmac

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token

from ignition_rest_mcp.config import Settings

READ_SCOPE = "ignition.read"
ADMIN_SCOPE = "ignition.admin"


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


@dataclass(frozen=True, slots=True)
class Principal:
    """A safe principal key plus verified scopes. Never a secret, never loggable as one."""

    key: str
    scopes: frozenset[str]
    auth_mode: str

    def has_scope(self, scope: str) -> bool:
        # D07: scopes have no implicit hierarchy; membership is the only rule.
        return scope in self.scopes


def principal_from_token(settings: Settings, token: AccessToken | None) -> Principal:
    """Derive the safe principal key from a *verified* access token (never from
    caller-supplied strings — the token must come from auth.py verification).

    ``auth=none`` and ``static-token`` are each a single trust domain (D18/D07-A):
    the configured service identity and the static-token client respectively.
    """

    if settings.auth_mode == "jwt" and token is not None:
        subject = token.subject or token.client_id
        return Principal(
            key=f"jwt:{subject}",
            scopes=frozenset(token.scopes),
            auth_mode="jwt",
        )
    if settings.auth_mode == "static-token" and token is not None:
        return Principal(
            key=f"static-token:{token.client_id}",
            scopes=frozenset(token.scopes),
            auth_mode="static-token",
        )
    if settings.auth_mode == "none" and token is None:
        return Principal(
            key=f"none:{settings.service_identity}",
            scopes=frozenset({READ_SCOPE}),
            auth_mode="none",
        )
    # Unauthenticated under an authentication mode must never happen through the
    # MCP middleware; fail to a scope-less identity rather than a trusted one.
    return Principal(key="unauthenticated", scopes=frozenset(), auth_mode=settings.auth_mode)


def current_principal(settings: Settings) -> Principal:
    return principal_from_token(settings, get_access_token())
