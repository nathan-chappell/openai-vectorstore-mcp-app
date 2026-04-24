from __future__ import annotations

import base64
import json
from typing import Literal

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token

from .clerk import ClerkAuthService
from .settings import AppSettings


class ClerkAccessToken(AccessToken):
    subject: str
    token_id: str | None = None
    session_id: str | None = None
    token_type: Literal["oauth_token", "session_token"]


class ClerkTokenVerifier(TokenVerifier):
    """FastMCP token verifier backed by Clerk session and OAuth access tokens."""

    def __init__(
        self,
        clerk_auth: ClerkAuthService,
        settings: AppSettings,
    ) -> None:
        super().__init__(
            base_url=settings.normalized_app_base_url,
            resource_base_url=f"{settings.normalized_app_base_url}/mcp",
            required_scopes=settings.mcp_required_scopes,
        )
        self._clerk_auth = clerk_auth
        self._settings = settings

    async def verify_token(self, token: str) -> ClerkAccessToken | None:
        if _looks_like_session_token(token):
            verified_session = await self._clerk_auth.verify_session_token(token)
            if verified_session is not None:
                claims = {
                    "sub": verified_session.subject,
                    "sid": verified_session.session_id,
                    "jti": verified_session.token_id,
                    "token_type": "session_token",
                }
                return ClerkAccessToken(
                    token=token,
                    client_id="clerk-session",
                    scopes=list(self._settings.mcp_required_scopes),
                    expires_at=int(verified_session.expiration) if verified_session.expiration else None,
                    subject=verified_session.subject,
                    token_id=verified_session.token_id,
                    session_id=verified_session.session_id,
                    token_type="session_token",
                    claims=claims,
                )

        verified_access = await self._clerk_auth.verify_access_token(token)
        if verified_access is None:
            return None

        claims = {
            "sub": verified_access.subject,
            "jti": verified_access.id,
            "client_id": verified_access.client_id,
            "scope": " ".join(verified_access.scopes),
            "token_type": "oauth_token",
        }
        return ClerkAccessToken(
            token=token,
            client_id=verified_access.client_id,
            scopes=verified_access.scopes,
            expires_at=int(verified_access.expiration) if verified_access.expiration else None,
            subject=verified_access.subject,
            token_id=verified_access.id,
            session_id=None,
            token_type="oauth_token",
            claims=claims,
        )


def get_current_clerk_access_token() -> ClerkAccessToken | None:
    """Return the authenticated Clerk token for the current MCP request, if any."""

    token = get_access_token()
    if token is None:
        return None
    if not isinstance(token, ClerkAccessToken):
        raise RuntimeError("Expected a ClerkAccessToken in the current request context.")
    return token


def _looks_like_session_token(token: str) -> bool:
    parts = token.split(".")
    if len(parts) != 3:
        return False

    try:
        payload = _decode_b64url_json(parts[1])
    except ValueError:
        return False

    return isinstance(payload.get("sid"), str) and isinstance(payload.get("sub"), str)


def _decode_b64url_json(value: str) -> dict[str, object]:
    padding = "=" * (-len(value) % 4)
    decoded = base64.urlsafe_b64decode(f"{value}{padding}")
    payload = json.loads(decoded.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object payload.")
    return payload
