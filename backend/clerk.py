from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Mapping

import httpx
from clerk_backend_api.sdk import Clerk
from clerk_backend_api.security.types import AuthenticateRequestOptions
from pydantic import BaseModel, Field

from .settings import AppSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClerkRequest:
    headers: Mapping[str, str]


class ClerkVerifiedAccessToken(BaseModel):
    object: str
    id: str
    client_id: str
    subject: str
    scopes: list[str] = Field(default_factory=list)
    revoked: bool = False
    expired: bool = False
    expiration: float | None = None


class ClerkVerifiedSessionToken(BaseModel):
    subject: str
    session_id: str
    token_id: str | None = None
    expiration: float | None = None


class ClerkUserRecord(BaseModel):
    clerk_user_id: str
    primary_email: str | None = None
    display_name: str
    active: bool = False
    role: str | None = None


class ClerkAuthService:
    """Minimal Clerk backend client for token verification and user activation checks."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._secret_key = settings.clerk_secret_key.get_secret_value()
        self._client = httpx.AsyncClient(
            base_url="https://api.clerk.com",
            headers={
                "Authorization": f"Bearer {self._secret_key}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
        self._sdk = Clerk(bearer_auth=self._secret_key)

    async def verify_access_token(self, token: str) -> ClerkVerifiedAccessToken | None:
        response = await self._client.post(
            "/oauth_applications/access_tokens/verify",
            json={"access_token": token},
        )
        if response.status_code == 200:
            payload = response.json()
            if payload.get("active") is False:
                return None
            verified = ClerkVerifiedAccessToken.model_validate(payload)
            if verified.revoked or verified.expired:
                return None
            return verified

        if response.status_code in {400, 401, 404}:
            logger.debug(
                "clerk_oauth_token_rejected status_code=%s",
                response.status_code,
            )
            return None

        response.raise_for_status()
        return None

    async def verify_session_token(self, token: str) -> ClerkVerifiedSessionToken | None:
        try:
            request_state = await self._sdk.authenticate_request_async(
                ClerkRequest(headers={"Authorization": f"Bearer {token}"}),
                AuthenticateRequestOptions(
                    secret_key=self._secret_key,
                    authorized_parties=self._settings.clerk_authorized_parties or None,
                    clock_skew_in_ms=self._settings.clerk_clock_skew_ms,
                ),
            )
        except Exception as error:
            logger.debug("clerk_session_token_rejected reason=%s", error)
            return None

        if not request_state.is_signed_in or request_state.payload is None:
            return None

        payload = request_state.payload
        subject = payload.get("sub")
        session_id = payload.get("sid")
        if not isinstance(subject, str) or not isinstance(session_id, str):
            return None

        token_id = payload.get("jti")
        expiration = payload.get("exp")
        return ClerkVerifiedSessionToken(
            subject=subject,
            session_id=session_id,
            token_id=token_id if isinstance(token_id, str) else None,
            expiration=float(expiration) if isinstance(expiration, int | float) else None,
        )

    async def get_user_record(self, clerk_user_id: str) -> ClerkUserRecord:
        response = await self._client.get(f"/v1/users/{clerk_user_id}")
        response.raise_for_status()

        payload = response.json()
        private_metadata = payload.get("private_metadata") or {}
        active = bool(private_metadata.get(self._settings.clerk_active_metadata_key))
        raw_role = private_metadata.get(self._settings.clerk_role_metadata_key)
        role = raw_role.strip() if isinstance(raw_role, str) and raw_role.strip() else None

        return ClerkUserRecord(
            clerk_user_id=clerk_user_id,
            primary_email=self._extract_primary_email(payload),
            display_name=self._extract_display_name(payload, clerk_user_id),
            active=active,
            role=role,
        )

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _extract_primary_email(payload: dict[str, Any]) -> str | None:
        primary_email_id = payload.get("primary_email_address_id")
        email_addresses = payload.get("email_addresses") or []
        for email in email_addresses:
            if not isinstance(email, dict):
                continue
            if email.get("id") == primary_email_id:
                address = email.get("email_address")
                return address if isinstance(address, str) else None
        for email in email_addresses:
            if isinstance(email, dict) and isinstance(email.get("email_address"), str):
                return email["email_address"]
        return None

    @classmethod
    def _extract_display_name(cls, payload: dict[str, Any], clerk_user_id: str) -> str:
        first_name = payload.get("first_name")
        last_name = payload.get("last_name")
        full_name = " ".join(
            part.strip() for part in [first_name, last_name] if isinstance(part, str) and part.strip()
        ).strip()
        if full_name:
            return full_name

        username = payload.get("username")
        if isinstance(username, str) and username.strip():
            return username

        email = cls._extract_primary_email(payload)
        if email:
            return email

        return clerk_user_id
