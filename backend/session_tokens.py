from __future__ import annotations

import time

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from .schemas import UploadSessionResult
from .settings import AppSettings


class UploadSessionClaims(BaseModel):
    clerk_user_id: str
    file_library_id: str


class FileDownloadClaims(BaseModel):
    clerk_user_id: str
    file_id: str


class FileLibrarySessionService:
    """Issue and verify short-lived signed tokens for web upload and download flows."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._serializer = URLSafeTimedSerializer(
            settings.app_signing_secret.get_secret_value(),
            salt="openai-file-desk",
        )

    def issue_upload_session(
        self,
        *,
        clerk_user_id: str,
        file_library_id: str,
    ) -> UploadSessionResult:
        token = self._serializer.dumps(
            {
                "kind": "upload",
                "clerk_user_id": clerk_user_id,
                "file_library_id": file_library_id,
            }
        )
        return UploadSessionResult(
            upload_url=f"{self._settings.normalized_app_base_url}/api/uploads",
            upload_token=token,
            expires_at=int(time.time()) + self._settings.upload_session_max_age_seconds,
        )

    def verify_upload_session(self, token: str) -> UploadSessionClaims | None:
        payload = self._loads(token, max_age=self._settings.upload_session_max_age_seconds)
        if payload is None or payload.get("kind") != "upload":
            return None
        return UploadSessionClaims.model_validate(payload)

    def issue_file_download_url(
        self,
        *,
        clerk_user_id: str,
        file_id: str,
    ) -> str:
        token = self._serializer.dumps(
            {
                "kind": "file-download",
                "clerk_user_id": clerk_user_id,
                "file_id": file_id,
            }
        )
        return f"{self._settings.normalized_app_base_url}/api/files/{file_id}/content?token={token}"

    def verify_file_download(self, token: str) -> FileDownloadClaims | None:
        payload = self._loads(
            token,
            max_age=self._settings.asset_download_session_max_age_seconds,
        )
        if payload is None or payload.get("kind") != "file-download":
            return None
        return FileDownloadClaims.model_validate(payload)

    def _loads(self, token: str, *, max_age: int) -> dict[str, object] | None:
        try:
            raw_payload = self._serializer.loads(token, max_age=max_age)
        except BadSignature, SignatureExpired:
            return None

        if not isinstance(raw_payload, dict):
            return None
        return raw_payload
