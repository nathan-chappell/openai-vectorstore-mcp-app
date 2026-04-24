from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from chatkit.server import StreamingResult
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .bootstrap import create_services
from .mcp_app import create_mcp_server
from .schemas import (
    DeleteFileResult,
    FileDetail,
    FileListResponse,
    TagListResponse,
    UploadFinalizeResult,
    UploadSessionResult,
)
from .settings import AppSettings, get_settings
from .web_auth import AuthenticatedWebUser, require_active_web_user


def create_fastapi_app(settings: AppSettings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    services = create_services(resolved_settings)
    mcp_server = create_mcp_server(resolved_settings, services)
    mcp_http_app = mcp_server.http_app(path="/", transport="streamable-http")
    static_dir = Path(resolved_settings.normalized_static_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.services = services
        app.state.mcp_server = mcp_server
        async with mcp_http_app.lifespan(mcp_http_app):
            yield

    app = FastAPI(title=resolved_settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],
    )
    app.mount("/mcp", mcp_http_app)

    @app.get("/health")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/files")
    async def list_files_api(
        user: AuthenticatedWebUser = Depends(require_active_web_user),
        query: str | None = Query(default=None, min_length=1),
        tag_ids: list[str] | None = Query(default=None),
        tag_match_mode: Literal["all", "any"] = Query(default="all"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> FileListResponse:
        return await services.file_library.list_files(
            clerk_user_id=user.clerk_user_id,
            query=query,
            tag_ids=tag_ids or [],
            tag_match_mode=tag_match_mode,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/files/{file_id}")
    async def get_file_detail_api(
        file_id: str,
        user: AuthenticatedWebUser = Depends(require_active_web_user),
    ) -> FileDetail:
        return await services.file_library.get_file_detail(
            clerk_user_id=user.clerk_user_id,
            file_id=file_id,
        )

    @app.delete("/api/files/{file_id}")
    async def delete_file_api(
        file_id: str,
        user: AuthenticatedWebUser = Depends(require_active_web_user),
    ) -> DeleteFileResult:
        return await services.file_library.delete_file(
            clerk_user_id=user.clerk_user_id,
            file_id=file_id,
        )

    @app.get("/api/tags")
    async def list_tags_api(
        user: AuthenticatedWebUser = Depends(require_active_web_user),
    ) -> TagListResponse:
        return await services.file_library.list_tags(clerk_user_id=user.clerk_user_id)

    @app.post("/api/uploads/session")
    async def issue_upload_session_api(
        user: AuthenticatedWebUser = Depends(require_active_web_user),
    ) -> UploadSessionResult:
        return await services.file_library.issue_upload_session(clerk_user_id=user.clerk_user_id)

    @app.post("/api/uploads")
    async def upload_file_api(
        file: UploadFile = File(...),
        upload_token: str = Form(...),
        tag_ids: list[str] | None = Form(default=None),
        user: AuthenticatedWebUser = Depends(require_active_web_user),
    ) -> UploadFinalizeResult:
        local_path = await _write_upload_to_tempfile(file)
        try:
            return await services.file_library.ingest_file_with_upload_token(
                clerk_user_id=user.clerk_user_id,
                upload_token=upload_token,
                local_path=local_path,
                filename=file.filename or "upload",
                declared_media_type=file.content_type,
                tag_ids=tag_ids or [],
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        finally:
            local_path.unlink(missing_ok=True)

    @app.get("/api/files/{file_id}/content")
    async def download_file_api(
        file_id: str,
        token: str = Query(..., min_length=1),
    ) -> Response:
        try:
            detail, payload = await services.file_library.download_file_with_token(
                file_id=file_id,
                token=token,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

        headers = {
            "Content-Disposition": f'attachment; filename="{detail.original_filename}"',
        }
        return Response(
            content=payload,
            media_type=detail.original_mime_type or detail.media_type,
            headers=headers,
        )

    @app.post("/api/chatkit")
    async def chatkit_entrypoint(
        request: Request,
        user: AuthenticatedWebUser = Depends(require_active_web_user),
    ) -> Response:
        raw_request = await request.body()
        context = await services.chatkit_server.build_request_context(
            raw_request,
            clerk_user_id=user.clerk_user_id,
            user_email=user.email,
            display_name=user.display_name,
            bearer_token=user.bearer_token,
            request_app=request.app,
        )
        result = await services.chatkit_server.process(raw_request, context)
        if isinstance(result, StreamingResult):
            return StreamingResponse(result, media_type="text/event-stream")
        return Response(content=result.json, media_type="application/json")

    @app.get("/{full_path:path}")
    async def spa_entrypoint(full_path: str) -> FileResponse:
        index_path = static_dir / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="Frontend build not found.")
        candidate = static_dir / full_path
        if full_path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_path)

    return app


async def _write_upload_to_tempfile(file: UploadFile) -> Path:
    suffix = Path(file.filename or "upload").suffix
    with NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
        temp_path = Path(temp_file.name)
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            temp_file.write(chunk)
    await file.close()
    return temp_path
