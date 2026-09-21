"""Caller authentication wiring and safe principal keys (D07/D07-A/D18)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hmac

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token

from ignition_rest_mcp.config import READ_SCOPE, Settings, StaticToken


class ConstantTimeStaticTokenVerifier(TokenVerifier):
    """D07 named static tokens: several credentials, each with its own scopes.

    The transport gate requires only a *verified* credential. Per-Tool scopes are
    authorized centrally by :mod:`ignition_rest_mcp.authorization`, which filters
    ``tools/list`` and denies again at call time — a coarse transport-wide scope
    gate cannot express per-token scopes without granting one token another's
    scope.
    """

    def __init__(self, tokens: Sequence[StaticToken]) -> None:
        super().__init__(required_scopes=None)
        # Pre-encoded: `hmac.compare_digest` rejects `str` holding non-ASCII, and
        # eager encoding keeps verification allocation-free per request.
        self._tokens: tuple[tuple[StaticToken, bytes], ...] = tuple(
            (entry, entry.token.encode("utf-8")) for entry in tokens
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        # Every configured token is compared, without an early exit, so a caller
        # cannot learn which position matched from response timing.
        candidate = token.encode("utf-8", "surrogatepass")
        matched: StaticToken | None = None
        for entry, encoded in self._tokens:
            if hmac.compare_digest(candidate, encoded):
                matched = entry
        if matched is None:
            return None
        return AccessToken(
            # The credential is never retained past verification (D07: the token
            # value never appears in logs, audit, errors or metrics).
            token="",
            client_id=matched.name,
            subject=matched.name,
            scopes=list(matched.scopes),
            claims={"deployment_profile": "trusted-internal", "tokenName": matched.name},
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
    if settings.auth_mode == "static-token":
        return ConstantTimeStaticTokenVerifier(settings.static_tokens)
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


#: Module-private construction token (slice 6): only ``auth.py`` — i.e. only code
#: that has just *verified a credential* — can mint a VerifiedPrincipal. The
#: guarded mutation executor accepts nothing else.
_PRINCIPAL_TOKEN: object = object()


class VerifiedPrincipal(Principal):
    """A principal produced solely by this module's verified-token paths."""

    __slots__ = ()

    def __new__(cls, *args: object, **kwargs: object) -> "VerifiedPrincipal":
        # Any direct construction (positional token, kwargs, whatever) must present
        # the private token; the dataclass __init__ is never usable for this class.
        if not args or args[0] is not _PRINCIPAL_TOKEN:
            raise TypeError(
                "VerifiedPrincipal may only be constructed by the authentication module "
                "from a just-verified credential"
            )
        return object.__new__(cls)

    @staticmethod
    def _mint(*, key: str, scopes: frozenset[str], auth_mode: str) -> "VerifiedPrincipal":
        instance = object.__new__(VerifiedPrincipal)
        object.__setattr__(instance, "key", key)
        object.__setattr__(instance, "scopes", scopes)
        object.__setattr__(instance, "auth_mode", auth_mode)
        return instance


def is_verified_principal(value: object) -> bool:
    """Exact type check: a forged or duck-typed principal is rejected."""

    return type(value) is VerifiedPrincipal


def principal_from_token(settings: Settings, token: AccessToken | None) -> VerifiedPrincipal:
    """Derive the safe principal key from a *verified* access token (never from
    caller-supplied strings — the token must come from auth.py verification).

    ``auth=none`` is one trust domain (the configured service identity); each named
    static token is its own, keyed by the token name (D07 Phase 4 amendment).
    Every path mints a :class:`VerifiedPrincipal`, so the mutation chain can accept
    the result of this function directly.
    """

    if settings.auth_mode == "jwt" and token is not None:
        subject = token.subject or token.client_id
        return VerifiedPrincipal._mint(
            key=f"jwt:{subject}",
            scopes=frozenset(token.scopes),
            auth_mode="jwt",
        )
    if settings.auth_mode == "static-token" and token is not None:
        return VerifiedPrincipal._mint(
            key=f"static-token:{token.client_id}",
            scopes=frozenset(token.scopes),
            auth_mode="static-token",
        )
    if settings.auth_mode == "none" and token is None:
        return VerifiedPrincipal._mint(
            key=f"none:{settings.service_identity}",
            scopes=frozenset({READ_SCOPE}),
            auth_mode="none",
        )
    # Unauthenticated under an authentication mode must never happen through the
    # MCP middleware; fail to a scope-less identity rather than a trusted one.
    return VerifiedPrincipal._mint(key="unauthenticated", scopes=frozenset(), auth_mode=settings.auth_mode)


def current_principal(settings: Settings) -> VerifiedPrincipal:
    """The current request's Mutation principal, minted from its verified credential."""

    return principal_from_token(settings, get_access_token())
