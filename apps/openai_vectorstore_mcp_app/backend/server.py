from __future__ import annotations

from base64 import b64decode
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from chatkit.server import StreamingResult
from fastmcp import FastMCP, FastMCPApp
from fastmcp.server.auth import MultiAuth
from fastmcp.server.auth.providers.clerk import ClerkProvider
from fastmcp.server.context import Context
from mcp.types import ToolAnnotations
from prefab_ui import PrefabApp
from prefab_ui.actions import SetState, ShowToast
from prefab_ui.actions.mcp import CallTool
from prefab_ui.components import (
    ERROR,
    RESULT,
    STATE,
    Badge,
    Button,
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
    Column,
    DropZone,
    Else,
    ForEach,
    H3,
    If,
    Muted,
    Row,
    Separator,
    Small,
    Text,
)
from pydantic import Field

from .auth import ClerkTokenVerifier, get_current_clerk_access_token
from .chat_store import FileDeskChatStore
from .chatkit_server import FileDeskChatKitServer
from .clerk import ClerkAuthService
from .db import DatabaseManager
from .file_library_service import DeleteFileResult, FileLibraryService, FileListResponse, TagListResponse
from .knowledge_base_service import KnowledgeBaseService
from .logging import configure_logging
from .openai_gateway import OpenAIKnowledgeBaseGateway
from .qa_agent import KnowledgeBaseQuestionAnswerer
from .schemas import KnowledgeNodeDetail, SearchHit, UploadFinalizeResult, UploadSessionResult
from .settings import AppSettings, get_settings
from .upload_sessions import KnowledgeBaseSessionService
from .web_auth import AuthenticatedWebUser, require_active_web_user

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AppServices:
    settings: AppSettings
    database: DatabaseManager
    clerk_auth: ClerkAuthService
    session_tokens: KnowledgeBaseSessionService
    openai_gateway: OpenAIKnowledgeBaseGateway
    question_answerer: KnowledgeBaseQuestionAnswerer
    knowledge_base_service: KnowledgeBaseService
    file_library: FileLibraryService
    chat_store: FileDeskChatStore
    chatkit_server: FileDeskChatKitServer
    _closed: bool = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.openai_gateway.close()
        await self.clerk_auth.close()
        await self.database.close()


def create_services(settings: AppSettings) -> AppServices:
    configure_logging(settings.log_level)
    database = DatabaseManager(settings)
    clerk_auth = ClerkAuthService(settings)
    session_tokens = KnowledgeBaseSessionService(settings)
    openai_gateway = OpenAIKnowledgeBaseGateway(settings)
    question_answerer = KnowledgeBaseQuestionAnswerer(settings)
    knowledge_base_service = KnowledgeBaseService(
        settings=settings,
        database=database,
        clerk_auth=clerk_auth,
        session_tokens=session_tokens,
        openai_gateway=openai_gateway,
        question_answerer=question_answerer,
    )
    file_library = FileLibraryService(
        database=database,
        clerk_auth=clerk_auth,
        session_tokens=session_tokens,
        openai_gateway=openai_gateway,
        legacy_service=knowledge_base_service,
    )
    chat_store = FileDeskChatStore(
        database=database,
        clerk_auth=clerk_auth,
        legacy_service=knowledge_base_service,
    )
    chatkit_server = FileDeskChatKitServer(
        settings=settings,
        store=chat_store,
        file_library=file_library,
    )
    return AppServices(
        settings=settings,
        database=database,
        clerk_auth=clerk_auth,
        session_tokens=session_tokens,
        openai_gateway=openai_gateway,
        question_answerer=question_answerer,
        knowledge_base_service=knowledge_base_service,
        file_library=file_library,
        chat_store=chat_store,
        chatkit_server=chatkit_server,
    )


def create_mcp_auth_provider(
    settings: AppSettings,
    clerk_auth: ClerkAuthService,
):
    session_verifier = ClerkTokenVerifier(clerk_auth, settings)
    if not settings.clerk_client_id:
        return session_verifier

    clerk_provider = ClerkProvider(
        domain=settings.effective_clerk_domain,
        client_id=settings.clerk_client_id,
        client_secret=settings.clerk_client_secret.get_secret_value()
        if settings.clerk_client_secret is not None
        else None,
        base_url=settings.normalized_app_base_url,
        resource_base_url=f"{settings.normalized_app_base_url}/mcp",
        issuer_url=str(settings.clerk_issuer_url),
        required_scopes=settings.mcp_required_scopes,
    )
    return MultiAuth(
        server=clerk_provider,
        verifiers=[session_verifier],
        base_url=settings.normalized_app_base_url,
        resource_base_url=f"{settings.normalized_app_base_url}/mcp",
        required_scopes=settings.mcp_required_scopes,
    )


def create_mcp_server(
    settings: AppSettings,
    services: AppServices,
) -> FastMCP:
    @asynccontextmanager
    async def server_lifespan(_: FastMCP[None]) -> AsyncIterator[None]:
        await services.database.ensure_ready()
        try:
            yield None
        finally:
            await services.close()

    server = FastMCP(
        name=settings.app_name,
        instructions=(
            "You are the MCP server for a personal file desk. Use open_file_library to surface "
            "the interactive upload/manage UI when the user wants to browse files visually. Use "
            "list_files, search_files, get_file_details, read_file_text, and list_tags to inspect "
            "the user's library. Only call delete_file after the user has explicitly confirmed "
            "that the file should be removed."
        ),
        auth=create_mcp_auth_provider(settings, services.clerk_auth),
        lifespan=server_lifespan,
    )

    @server.tool(
        name="list_files",
        description="List the user's uploaded files with optional text and tag filtering.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def list_files_tool(
        query: Annotated[str | None, Field(min_length=1)] = None,
        tag_ids: list[str] | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
        page: Annotated[int, Field(ge=1)] = 1,
        page_size: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> FileListResponse:
        return await services.file_library.list_files(
            clerk_user_id=_current_mcp_clerk_user_id(),
            query=query,
            tag_ids=tag_ids or [],
            tag_match_mode=tag_match_mode,
            page=page,
            page_size=page_size,
        )

    @server.tool(
        name="list_tags",
        description="List the user's available file tags.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def list_tags_tool() -> TagListResponse:
        return await services.file_library.list_tags(
            clerk_user_id=_current_mcp_clerk_user_id(),
        )

    @server.tool(
        name="search_files",
        description=(
            "Run semantic search over the user's uploaded files. This is the best tool when the "
            "user describes content but not the exact filename."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def search_files_tool(
        query: Annotated[str, Field(min_length=1)],
        tag_ids: list[str] | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
        max_results: Annotated[int, Field(ge=1, le=20)] = 8,
    ) -> list[SearchHit]:
        return await services.file_library.search_files(
            clerk_user_id=_current_mcp_clerk_user_id(),
            query=query,
            tag_ids=tag_ids or [],
            tag_match_mode=tag_match_mode,
            max_results=max_results,
        )

    @server.tool(
        name="get_file_details",
        description="Load full metadata and derived artifact details for one uploaded file.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def get_file_details_tool(
        file_id: Annotated[str, Field(min_length=1)],
    ) -> KnowledgeNodeDetail:
        return await services.file_library.get_file_detail(
            clerk_user_id=_current_mcp_clerk_user_id(),
            file_id=file_id,
        )

    @server.tool(
        name="read_file_text",
        description=(
            "Read extracted text for one uploaded file. Use this after you know which file is "
            "relevant and need more of its actual contents."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def read_file_text_tool(
        file_id: Annotated[str, Field(min_length=1)],
        max_chars: Annotated[int, Field(ge=250, le=20_000)] = 12_000,
    ) -> str:
        return await services.file_library.read_file_text(
            clerk_user_id=_current_mcp_clerk_user_id(),
            file_id=file_id,
            max_chars=max_chars,
        )

    @server.tool(
        name="delete_file",
        description=(
            "Delete one uploaded file. Only call this after the user has explicitly confirmed the "
            "deletion in the current conversation by setting confirm=true."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    async def delete_file_tool(
        file_id: Annotated[str, Field(min_length=1)],
        confirm: bool = False,
    ) -> DeleteFileResult | dict[str, object]:
        if not confirm:
            return {
                "confirmation_required": True,
                "file_id": file_id,
                "message": (
                    "Deletion requires explicit confirmation. Ask the user to confirm, then call "
                    "delete_file again with confirm=true."
                ),
            }
        return await services.file_library.delete_file(
            clerk_user_id=_current_mcp_clerk_user_id(),
            file_id=file_id,
        )

    file_app = FastMCPApp("File Library")

    @file_app.tool("refresh_files")
    async def refresh_files_tool(ctx: Context) -> list[dict[str, Any]]:
        del ctx
        return [
            item.model_dump(mode="json")
            for item in (
                await services.file_library.list_files(
                    clerk_user_id=_current_mcp_clerk_user_id(),
                    query=None,
                    tag_ids=[],
                    tag_match_mode="all",
                    page=1,
                    page_size=50,
                )
            ).files
        ]

    @file_app.tool("upload_files")
    async def upload_files_tool(
        files: list[dict[str, object]],
        ctx: Context,
    ) -> list[dict[str, Any]]:
        for file_payload in files:
            await _ingest_browser_uploaded_file(
                services=services,
                clerk_user_id=_current_mcp_clerk_user_id(),
                file_payload=file_payload,
            )
        return await refresh_files_tool(ctx)

    @file_app.tool("delete_file_from_ui")
    async def delete_file_from_ui_tool(
        file_id: str,
        ctx: Context,
    ) -> list[dict[str, Any]]:
        await services.file_library.delete_file(
            clerk_user_id=_current_mcp_clerk_user_id(),
            file_id=file_id,
        )
        return await refresh_files_tool(ctx)

    @file_app.ui(
        name="open_file_library",
        title="Open File Library",
        description="Open an interactive file library UI for uploading and managing files.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def open_file_library(ctx: Context) -> PrefabApp:
        existing_files = await refresh_files_tool(ctx)
        with Card(css_class="max-w-4xl mx-auto") as view:
            with CardHeader(), Column(gap=1):
                CardTitle("File Library")
                CardDescription(
                    "Upload a few files, keep an eye on what is already stored, and delete "
                    "anything you no longer want the assistant to use."
                )

            with CardContent(), Column(gap=4):
                with Row(gap=2, align="center"):
                    H3("Upload")
                    Button(
                        "Refresh",
                        on_click=CallTool(
                            "refresh_files",
                            on_success=SetState("files", RESULT),
                            on_error=ShowToast(ERROR, variant="error"),
                        ),
                    )

                DropZone(
                    name="pending",
                    icon="folder-up",
                    label="Drop files here",
                    description="Any file type, up to 25MB per file.",
                    multiple=True,
                    max_size=25 * 1024 * 1024,
                )

                with If(STATE.pending.length()), Column(gap=2):
                    with ForEach("pending") as pending_file, Row(gap=2, align="center"):
                        Small(pending_file.name)  # ty:ignore[invalid-argument-type]
                        Muted(pending_file.type)  # ty:ignore[invalid-argument-type]
                    Button(
                        "Upload to library",
                        on_click=CallTool(
                            "upload_files",
                            arguments={"files": STATE.pending},
                            on_success=[
                                SetState("files", RESULT),
                                SetState("pending", []),
                                ShowToast("Files uploaded.", variant="success"),
                            ],
                            on_error=ShowToast(ERROR, variant="error"),
                        ),
                    )

                Separator()
                with Row(gap=2, align="center"):
                    H3("Stored Files")
                    with If(STATE.files.length()):
                        Badge(STATE.files.length(), variant="secondary")  # ty:ignore[invalid-argument-type]

                with If(STATE.files.length()), Column(gap=2):
                    with (
                        ForEach("files") as file_row,
                        Row(
                            gap=3,
                            align="center",
                            css_class="justify-between rounded-xl border border-slate-200 px-3 py-3",
                        ),
                    ):
                        with Column(gap=0):
                            Small(file_row.display_title)  # ty:ignore[invalid-argument-type]
                            Muted(file_row.original_filename)  # ty:ignore[invalid-argument-type]
                        with Row(gap=2, align="center"):
                            Badge(file_row.media_type, variant="outline")  # ty:ignore[invalid-argument-type]
                            Badge(file_row.status, variant="secondary")  # ty:ignore[invalid-argument-type]
                            Button(
                                "Delete",
                                on_click=CallTool(
                                    "delete_file_from_ui",
                                    arguments={"file_id": file_row.id},
                                    on_success=[
                                        SetState("files", RESULT),
                                        ShowToast("File deleted.", variant="success"),
                                    ],
                                    on_error=ShowToast(ERROR, variant="error"),
                                ),
                            )

                with Else(), Column(gap=1):
                    Text("No files uploaded yet.")
                    Muted("Once files land here, the assistant can search and read them.")

        return PrefabApp(
            title="File Library",
            view=view,
            state={
                "pending": [],
                "files": existing_files,
            },
        )

    server.add_provider(file_app)
    return server


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
    ) -> KnowledgeNodeDetail:
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
        claims = services.session_tokens.verify_upload_session(upload_token)
        if claims is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid upload token.")
        if claims.clerk_user_id != user.clerk_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Upload token does not belong to this user."
            )
        local_path = await _write_upload_to_tempfile(file)
        try:
            return await services.knowledge_base_service.ingest_upload(
                claims=claims,
                local_path=local_path,
                filename=file.filename or "upload",
                declared_media_type=file.content_type,
                tag_ids=tag_ids or [],
            )
        finally:
            local_path.unlink(missing_ok=True)

    @app.get("/api/nodes/{node_id}/content")
    async def download_file_api(
        node_id: str,
        token: str = Query(..., min_length=1),
    ) -> Response:
        claims = services.session_tokens.verify_node_download(token)
        if claims is None or claims.node_id != node_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid download token.")
        detail, payload = await services.knowledge_base_service.download_node_bytes(claims=claims)
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


async def _ingest_browser_uploaded_file(
    *,
    services: AppServices,
    clerk_user_id: str,
    file_payload: dict[str, object],
) -> None:
    filename = _required_string(file_payload, "name")
    encoded_data = _required_string(file_payload, "data")
    media_type = _optional_string(file_payload, "type")
    suffix = Path(filename).suffix
    with NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(b64decode(encoded_data))
    try:
        await services.file_library.ingest_file_for_user(
            clerk_user_id=clerk_user_id,
            local_path=temp_path,
            filename=filename,
            declared_media_type=media_type,
            tag_ids=[],
        )
    finally:
        temp_path.unlink(missing_ok=True)


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


def _current_mcp_clerk_user_id() -> str:
    token = get_current_clerk_access_token()
    if token is None:
        return "local-dev"
    return token.subject


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Expected a non-empty string field: {key}")
    return value.strip()


def _optional_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
