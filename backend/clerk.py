from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
import jwt
from jwt import InvalidTokenError
from jwt.algorithms import RSAAlgorithm
from pydantic import BaseModel, Field

from .settings import AppSettings

logger = logging.getLogger(__name__)

_JWKS_TTL_SECONDS = 300.0


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
        self._client = httpx.AsyncClient(
            base_url="https://api.clerk.com",
            headers={
                "Authorization": f"Bearer {settings.clerk_secret_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
        self._jwks_lock = asyncio.Lock()
        self._jwks_by_kid: dict[str, dict[str, Any]] = {}
        self._jwks_cached_at = 0.0

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
            header = jwt.get_unverified_header(token)
        except InvalidTokenError:
            return None

        key_id = header.get("kid")
        algorithm = header.get("alg")
        if not isinstance(key_id, str) or not key_id:
            return None

        jwk = await self._get_jwk(key_id)
        if jwk is None:
            logger.warning("clerk_session_token_missing_jwk kid=%s", key_id)
            return None

        if not isinstance(algorithm, str) or not algorithm:
            algorithm = "RS256"

        try:
            payload = jwt.decode(
                token,
                key=RSAAlgorithm.from_jwk(json.dumps(jwk)),
                algorithms=[algorithm],
                options={
                    "require": ["sub", "sid", "exp"],
                    "verify_aud": False,
                },
            )
        except InvalidTokenError as error:
            logger.debug("clerk_session_token_rejected reason=%s", error)
            return None

        subject = payload.get("sub")
        session_id = payload.get("sid")
        if not isinstance(subject, str) or not isinstance(session_id, str):
            return None

        token_issuer = payload.get("iss")
        configured_issuer = str(self._settings.clerk_issuer_url)
        if isinstance(token_issuer, str) and token_issuer:
            if _normalize_urlish(token_issuer) != _normalize_urlish(configured_issuer):
                logger.warning(
                    "clerk_session_token_issuer_mismatch configured_issuer=%s token_issuer=%s accepting_signed_token=true",
                    configured_issuer,
                    token_issuer,
                )

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

    async def _get_jwk(self, key_id: str) -> dict[str, Any] | None:
        jwks = await self._get_jwks()
        jwk = jwks.get(key_id)
        if jwk is not None:
            return jwk

        jwks = await self._get_jwks(force_refresh=True)
        return jwks.get(key_id)

    async def _get_jwks(self, *, force_refresh: bool = False) -> dict[str, dict[str, Any]]:
        now = time.time()
        if not force_refresh and self._jwks_by_kid and now - self._jwks_cached_at < _JWKS_TTL_SECONDS:
            return self._jwks_by_kid

        async with self._jwks_lock:
            now = time.time()
            if not force_refresh and self._jwks_by_kid and now - self._jwks_cached_at < _JWKS_TTL_SECONDS:
                return self._jwks_by_kid

            response = await self._client.get("/v1/jwks")
            response.raise_for_status()
            payload = response.json()
            keys = payload.get("keys") if isinstance(payload, dict) else None
            if not isinstance(keys, list):
                raise RuntimeError("Clerk JWKS response did not include a key list.")

            self._jwks_by_kid = {
                key["kid"]: key for key in keys if isinstance(key, dict) and isinstance(key.get("kid"), str)
            }
            self._jwks_cached_at = time.time()
            return self._jwks_by_kid

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


def _normalize_urlish(value: str) -> str:
    return value.rstrip("/")
