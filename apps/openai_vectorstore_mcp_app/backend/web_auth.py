from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .clerk import ClerkUserRecord

_http_bearer = HTTPBearer(auto_error=False)


@dataclass(slots=True)
class AuthenticatedWebUser:
    clerk_user_id: str
    email: str | None
    display_name: str
    active: bool
    role: str | None
    bearer_token: str
    token_type: Literal["oauth_token", "session_token"]


async def require_authenticated_web_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
) -> AuthenticatedWebUser:
    if credentials is None or not credentials.credentials.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
        )

    token = credentials.credentials.strip()
    services = request.app.state.services
    clerk_auth = services.clerk_auth

    session_token = await clerk_auth.verify_session_token(token)
    token_type: Literal["oauth_token", "session_token"]
    clerk_user_id: str
    if session_token is not None:
        token_type = "session_token"
        clerk_user_id = session_token.subject
    else:
        access_token = await clerk_auth.verify_access_token(token)
        if access_token is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid bearer token.",
            )
        token_type = "oauth_token"
        clerk_user_id = access_token.subject

    user_record = await clerk_auth.get_user_record(clerk_user_id)
    return _to_authenticated_web_user(
        user_record=user_record,
        bearer_token=token,
        token_type=token_type,
    )


async def require_active_web_user(
    request: Request,
    user: AuthenticatedWebUser = Depends(require_authenticated_web_user),
) -> AuthenticatedWebUser:
    if not user.active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is signed in but is still pending manual activation.",
        )
    return user


def _to_authenticated_web_user(
    *,
    user_record: ClerkUserRecord,
    bearer_token: str,
    token_type: Literal["oauth_token", "session_token"],
) -> AuthenticatedWebUser:
    return AuthenticatedWebUser(
        clerk_user_id=user_record.clerk_user_id,
        email=user_record.primary_email,
        display_name=user_record.display_name,
        active=user_record.active,
        role=user_record.role,
        bearer_token=bearer_token,
        token_type=token_type,
    )
