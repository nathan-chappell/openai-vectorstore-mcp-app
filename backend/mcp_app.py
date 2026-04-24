from __future__ import annotations

from base64 import b64decode
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Any, Literal

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
from .bootstrap import AppServices
from .clerk import ClerkAuthService
from .schemas import DeleteFileResult, FileDetail, FileListResponse, SearchHit, TagListResponse
from .settings import AppSettings


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
    return _build_mcp_server(
        settings=settings,
        services=services,
        auth=create_mcp_auth_provider(settings, services.clerk_auth),
    )


def create_dev_mcp_server(
    settings: AppSettings,
    services: AppServices,
) -> FastMCP:
    return _build_mcp_server(
        settings=settings,
        services=services,
        auth=None,
    )


def _build_mcp_server(
    *,
    settings: AppSettings,
    services: AppServices,
    auth: Any | None,
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
            "the interactive upload and file-management UI when the user wants to browse files "
            "visually. Use list_files, search_files, get_file_detail, read_file_text, and "
            "list_tags to inspect the user's file library. Only call delete_file after the user "
            "has explicitly confirmed that the file should be removed."
        ),
        auth=auth,
        lifespan=server_lifespan,
    )
    _register_mcp_routes(server=server, services=services)
    return server


def _register_mcp_routes(
    *,
    server: FastMCP,
    services: AppServices,
) -> None:
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
        name="get_file_detail",
        description="Load full metadata and derived artifact details for one uploaded file.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def get_file_detail_tool(
        file_id: Annotated[str, Field(min_length=1)],
    ) -> FileDetail:
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

    _register_file_app(server=server, services=services)


def _register_file_app(
    *,
    server: FastMCP,
    services: AppServices,
) -> None:
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
