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
from prefab_ui.components.form import Form
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
    Link,
    Muted,
    Row,
    Separator,
    Small,
    Text,
)
from pydantic import BaseModel, Field, create_model

from .auth import ClerkTokenVerifier, get_current_clerk_access_token
from .bootstrap import AppServices
from .clerk import ClerkAuthService
from .schemas import DeleteFileResult, FileDetail, FileListResponse, SearchBranchResponse, SearchHit, TagListResponse
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
            "You are the MCP server for a personal file desk. Each user has one personal file "
            "corpus. Use files to surface the interactive upload and file-management UI when the "
            "user wants to browse files visually. Use file_search to surface an interactive "
            "semantic search UI over the current user's files. Use branch_search to iteratively "
            "expand semantic search through similar files, optionally scoped by one or more tags. "
            "Use list_files, search_files, search_file_branches, get_file_detail, read_file_text, "
            "and list_tags to inspect the user's files. Only call delete_file after the user has "
            "explicitly confirmed that the file should be removed."
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
        name="search_file_branches",
        description=(
            "Iteratively expand semantic search through similar files in the user's library. "
            "Useful when you want a few rounds of related documents, optionally scoped by tags."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def search_file_branches_tool(
        query: Annotated[str, Field(min_length=1)],
        tag_ids: list[str] | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
        descend: Annotated[int, Field(ge=0, le=4)] = 2,
        max_width: Annotated[int, Field(ge=1, le=8)] = 3,
    ) -> SearchBranchResponse:
        return await services.file_library.search_file_branches(
            clerk_user_id=_current_mcp_clerk_user_id(),
            query=query,
            tag_ids=tag_ids or [],
            tag_match_mode=tag_match_mode,
            descend=descend,
            max_width=max_width,
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
    file_app = FastMCPApp("Files")

    async def available_tags_for_ui() -> list[dict[str, Any]]:
        return [
            tag.model_dump(mode="json")
            for tag in (await services.file_library.list_tags(clerk_user_id=_current_mcp_clerk_user_id())).tags
        ]

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

    @file_app.tool("search_files_for_ui")
    async def search_files_for_ui_tool(
        query: str | None,
        ctx: Context,
        data: dict[str, object] | None = None,
        tag_ids: list[str] | None = None,
        tag_filter: str | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
        max_results: Annotated[int, Field(ge=1, le=20)] = 8,
    ) -> dict[str, Any]:
        del ctx
        resolved_query, resolved_tag_ids, resolved_tag_match_mode = await _resolve_search_inputs(
            services=services,
            clerk_user_id=_current_mcp_clerk_user_id(),
            query=query,
            data=data,
            tag_ids=tag_ids,
            tag_filter=tag_filter,
            tag_match_mode=tag_match_mode,
        )
        normalized_query = resolved_query.strip()
        if not normalized_query:
            return {
                "query": normalized_query,
                "tag_ids": resolved_tag_ids,
                "tag_match_mode": resolved_tag_match_mode,
                "results": [],
            }
        return {
            "query": normalized_query,
            "tag_ids": resolved_tag_ids,
            "tag_match_mode": resolved_tag_match_mode,
            "results": [
                hit.model_dump(mode="json")
                for hit in await services.file_library.search_files(
                    clerk_user_id=_current_mcp_clerk_user_id(),
                    query=normalized_query,
                    tag_ids=resolved_tag_ids,
                    tag_match_mode=resolved_tag_match_mode,
                    max_results=max_results,
                )
            ],
        }

    @file_app.tool("search_file_branches_for_ui")
    async def search_file_branches_for_ui_tool(
        query: str | None,
        ctx: Context,
        data: dict[str, object] | None = None,
        tag_ids: list[str] | None = None,
        tag_filter: str | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
        descend: Annotated[int, Field(ge=0, le=4)] = 2,
        max_width: Annotated[int, Field(ge=1, le=8)] = 3,
    ) -> dict[str, Any]:
        del ctx
        resolved_query, resolved_tag_ids, resolved_tag_match_mode = await _resolve_search_inputs(
            services=services,
            clerk_user_id=_current_mcp_clerk_user_id(),
            query=query,
            data=data,
            tag_ids=tag_ids,
            tag_filter=tag_filter,
            tag_match_mode=tag_match_mode,
        )
        normalized_query = resolved_query.strip()
        resolved_descend = _form_int(data, "descend", default=descend) if data is not None else descend
        resolved_max_width = _form_int(data, "max_width", default=max_width) if data is not None else max_width
        branch_result = await services.file_library.search_file_branches(
            clerk_user_id=_current_mcp_clerk_user_id(),
            query=normalized_query,
            tag_ids=resolved_tag_ids,
            tag_match_mode=resolved_tag_match_mode,
            descend=resolved_descend,
            max_width=resolved_max_width,
        )
        return branch_result.model_dump(mode="json")

    @file_app.tool("load_file_preview_for_ui")
    async def load_file_preview_for_ui_tool(
        file_id: str,
        max_chars: Annotated[int, Field(ge=250, le=20_000)] = 8_000,
    ) -> dict[str, Any]:
        detail = await services.file_library.get_file_detail(
            clerk_user_id=_current_mcp_clerk_user_id(),
            file_id=file_id,
        )
        text = await services.file_library.read_file_text(
            clerk_user_id=_current_mcp_clerk_user_id(),
            file_id=file_id,
            max_chars=max_chars,
        )
        return {
            "detail": detail.model_dump(mode="json"),
            "text": text,
        }

    @file_app.ui(
        name="files",
        title="Files",
        description="Interactive files UI for uploading, previewing, and managing the current user's files.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def files(ctx: Context) -> PrefabApp:
        existing_files = await refresh_files_tool(ctx)
        with Card(css_class="max-w-4xl mx-auto") as view:
            with CardHeader(), Column(gap=1):
                CardTitle("Files")
                CardDescription(
                    "Manage the current user's file corpus: upload new files, preview stored "
                    "content, and delete anything you no longer want the assistant to use."
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
                                "Preview",
                                variant="outline",
                                on_click=CallTool(
                                    "load_file_preview_for_ui",
                                    arguments={"file_id": file_row.id},
                                    on_success=SetState("selected_preview", RESULT),
                                    on_error=ShowToast(ERROR, variant="error"),
                                ),
                            )
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

                Separator()
                with If(STATE.selected_preview), Card(css_class="border border-slate-200"):
                    with CardHeader(), Column(gap=1):
                        CardTitle(STATE.selected_preview.detail.display_title)  # ty:ignore[invalid-argument-type]
                        CardDescription(
                            STATE.selected_preview.detail.original_filename  # ty:ignore[invalid-argument-type]
                        )
                    with CardContent(), Column(gap=3):
                        with Row(gap=2, align="center"):
                            Badge(STATE.selected_preview.detail.media_type, variant="outline")  # ty:ignore[invalid-argument-type]
                            Badge(STATE.selected_preview.detail.status, variant="secondary")  # ty:ignore[invalid-argument-type]
                        Text(
                            content=STATE.selected_preview.text,  # ty:ignore[invalid-argument-type]
                            cssClass="text-sm whitespace-pre-wrap leading-6",
                        )
                        with If(STATE.selected_preview.detail.download_url):  # ty:ignore[invalid-argument-type]
                            Link(
                                "Download original",
                                href=STATE.selected_preview.detail.download_url,  # ty:ignore[invalid-argument-type]
                            )

        return PrefabApp(
            title="Files",
            view=view,
            state={
                "pending": [],
                "files": existing_files,
                "selected_preview": None,
            },
        )

    @file_app.ui(
        name="file_search",
        title="File Search",
        description="Search the current user's files and preview matching files.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def file_search(
        ctx: Context,
        query: Annotated[str | None, Field(min_length=1)] = None,
        tag_ids: list[str] | None = None,
        tag_filter: str | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
    ) -> PrefabApp:
        normalized_query = query.strip() if query else ""
        initial_search_state = await search_files_for_ui_tool(
            normalized_query,
            tag_ids=tag_ids,
            tag_filter=tag_filter,
            tag_match_mode=tag_match_mode,
            ctx=ctx,
        )
        initial_tags = await available_tags_for_ui()
        search_form_model = _build_search_form_model(
            name="FileSearchForm",
            available_tags=initial_tags,
            query=normalized_query,
            selected_tag_ids=initial_search_state["tag_ids"],
            tag_match_mode=initial_search_state["tag_match_mode"],
        )
        search_form_arguments = _build_search_form_arguments(
            available_tags=initial_tags,
            include_branch_controls=False,
        )

        with Card(css_class="max-w-4xl mx-auto") as view:
            with CardHeader(), Column(gap=1):
                CardTitle("File Search")
                CardDescription(
                    "Run semantic search across the current user's files, optionally scoped by "
                    "one or more tags, then preview the most relevant file without leaving the MCP app."
                )

            with CardContent(), Column(gap=4):
                Small("Tag filters are multi-select. Choose one or more tags to narrow the search.")
                Form.from_model(
                    search_form_model,
                    on_submit=CallTool(
                        "search_files_for_ui",
                        arguments={"data": search_form_arguments},
                        on_success=[
                            SetState("search_state", RESULT),
                            SetState("selected_preview", None),
                        ],
                        on_error=ShowToast(ERROR, variant="error"),
                    ),
                    submit_label="Search",
                )

                with If(STATE.search_state.results.length()), Column(gap=3):
                    with ForEach(STATE.search_state.results) as search_hit, Card(css_class="border border-slate-200"):
                        with CardHeader(), Column(gap=1):
                            CardTitle(search_hit.file_title)  # ty:ignore[invalid-argument-type]
                            CardDescription(search_hit.original_filename)  # ty:ignore[invalid-argument-type]
                        with CardContent(), Column(gap=3):
                            with Row(gap=2, align="center"):
                                Badge(search_hit.media_type, variant="outline")  # ty:ignore[invalid-argument-type]
                                with If(search_hit.tags.length()):  # ty:ignore[invalid-argument-type]
                                    with ForEach(search_hit.tags) as tag_name:  # ty:ignore[invalid-argument-type]
                                        Badge(tag_name, variant="secondary")  # ty:ignore[invalid-argument-type]
                            Text(
                                content=search_hit.text,  # ty:ignore[invalid-argument-type]
                                cssClass="text-sm whitespace-pre-wrap leading-6",
                            )
                            Button(
                                "Preview File",
                                variant="outline",
                                on_click=CallTool(
                                    "load_file_preview_for_ui",
                                    arguments={"file_id": search_hit.file_id},
                                    on_success=SetState("selected_preview", RESULT),
                                    on_error=ShowToast(ERROR, variant="error"),
                                ),
                            )

                with Else(), Column(gap=1):
                    Text("No search results yet.")
                    Muted("Enter a query to search across the current user's files.")

                Separator()
                with If(STATE.selected_preview), Card(css_class="border border-slate-200"):
                    with CardHeader(), Column(gap=1):
                        CardTitle(STATE.selected_preview.detail.display_title)  # ty:ignore[invalid-argument-type]
                        CardDescription(
                            STATE.selected_preview.detail.original_filename  # ty:ignore[invalid-argument-type]
                        )
                    with CardContent(), Column(gap=3):
                        with Row(gap=2, align="center"):
                            Badge(STATE.selected_preview.detail.media_type, variant="outline")  # ty:ignore[invalid-argument-type]
                            Badge(STATE.selected_preview.detail.status, variant="secondary")  # ty:ignore[invalid-argument-type]
                        Text(
                            content=STATE.selected_preview.text,  # ty:ignore[invalid-argument-type]
                            cssClass="text-sm whitespace-pre-wrap leading-6",
                        )
                        with If(STATE.selected_preview.detail.download_url):  # ty:ignore[invalid-argument-type]
                            Link(
                                "Download original",
                                href=STATE.selected_preview.detail.download_url,  # ty:ignore[invalid-argument-type]
                            )

        return PrefabApp(
            title="File Search",
            view=view,
            state={
                "search_state": initial_search_state,
                "selected_preview": None,
            },
        )

    @file_app.ui(
        name="branch_search",
        title="Branch Search",
        description="Iteratively expand similarity search through the current user's files.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def branch_search(
        ctx: Context,
        query: Annotated[str | None, Field(min_length=1)] = None,
        tag_ids: list[str] | None = None,
        tag_filter: str | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
        descend: Annotated[int, Field(ge=0, le=4)] = 2,
        max_width: Annotated[int, Field(ge=1, le=8)] = 3,
    ) -> PrefabApp:
        normalized_query = query.strip() if query else ""
        initial_branch_state = await search_file_branches_for_ui_tool(
            normalized_query,
            tag_ids=tag_ids,
            tag_filter=tag_filter,
            tag_match_mode=tag_match_mode,
            descend=descend,
            max_width=max_width,
            ctx=ctx,
        )
        initial_tags = await available_tags_for_ui()
        branch_form_model = _build_search_form_model(
            name="BranchSearchForm",
            available_tags=initial_tags,
            query=normalized_query,
            selected_tag_ids=initial_branch_state["tag_ids"],
            tag_match_mode=initial_branch_state["tag_match_mode"],
            descend=initial_branch_state["descend"],
            max_width=initial_branch_state["max_width"],
        )
        branch_form_arguments = _build_search_form_arguments(
            available_tags=initial_tags,
            include_branch_controls=True,
        )

        with Card(css_class="max-w-5xl mx-auto") as view:
            with CardHeader(), Column(gap=1):
                CardTitle("Branch Search")
                CardDescription(
                    "Start with one semantic query, then iterate on the strongest matches to "
                    "surface a few rounds of similar files. Multi-selected tag filters stay in "
                    "force at every step."
                )

            with CardContent(), Column(gap=4):
                Small("Tag filters are multi-select. Choose one or more tags to keep every branch scoped.")
                Form.from_model(
                    branch_form_model,
                    on_submit=CallTool(
                        "search_file_branches_for_ui",
                        arguments={"data": branch_form_arguments},
                        on_success=[
                            SetState("branch_state", RESULT),
                            SetState("selected_preview", None),
                        ],
                        on_error=ShowToast(ERROR, variant="error"),
                    ),
                    submit_label="Explore Branches",
                )

                with If(STATE.branch_state.levels.length()), Column(gap=3):
                    with ForEach(STATE.branch_state.levels) as branch_level, Card(css_class="border border-slate-200"):
                        with CardHeader(), Row(gap=2, align="center"):
                            CardTitle("Similarity Layer")
                            Badge(branch_level.depth, variant="outline")  # ty:ignore[invalid-argument-type]
                        with CardContent(), Column(gap=3):
                            with ForEach(branch_level.hits) as branch_hit, Card(css_class="border border-slate-100"):
                                with CardHeader(), Column(gap=1):
                                    CardTitle(branch_hit.file_title)  # ty:ignore[invalid-argument-type]
                                    CardDescription(branch_hit.original_filename)  # ty:ignore[invalid-argument-type]
                                with CardContent(), Column(gap=3):
                                    with Row(gap=2, align="center"):
                                        Badge(branch_hit.media_type, variant="outline")  # ty:ignore[invalid-argument-type]
                                        with If(branch_hit.tags.length()):  # ty:ignore[invalid-argument-type]
                                            with ForEach(branch_hit.tags) as tag_name:  # ty:ignore[invalid-argument-type]
                                                Badge(tag_name, variant="secondary")  # ty:ignore[invalid-argument-type]
                                    Text(
                                        content=branch_hit.text,  # ty:ignore[invalid-argument-type]
                                        cssClass="text-sm whitespace-pre-wrap leading-6",
                                    )
                                    Button(
                                        "Preview File",
                                        variant="outline",
                                        on_click=CallTool(
                                            "load_file_preview_for_ui",
                                            arguments={"file_id": branch_hit.file_id},
                                            on_success=SetState("selected_preview", RESULT),
                                            on_error=ShowToast(ERROR, variant="error"),
                                        ),
                                    )

                with Else(), Column(gap=1):
                    Text("No branch results yet.")
                    Muted("Run a query to expand a few similarity layers through the current user's files.")

                Separator()
                with If(STATE.selected_preview), Card(css_class="border border-slate-200"):
                    with CardHeader(), Column(gap=1):
                        CardTitle(STATE.selected_preview.detail.display_title)  # ty:ignore[invalid-argument-type]
                        CardDescription(
                            STATE.selected_preview.detail.original_filename  # ty:ignore[invalid-argument-type]
                        )
                    with CardContent(), Column(gap=3):
                        with Row(gap=2, align="center"):
                            Badge(STATE.selected_preview.detail.media_type, variant="outline")  # ty:ignore[invalid-argument-type]
                            Badge(STATE.selected_preview.detail.status, variant="secondary")  # ty:ignore[invalid-argument-type]
                        Text(
                            content=STATE.selected_preview.text,  # ty:ignore[invalid-argument-type]
                            cssClass="text-sm whitespace-pre-wrap leading-6",
                        )
                        with If(STATE.selected_preview.detail.download_url):  # ty:ignore[invalid-argument-type]
                            Link(
                                "Download original",
                                href=STATE.selected_preview.detail.download_url,  # ty:ignore[invalid-argument-type]
                            )

        return PrefabApp(
            title="Branch Search",
            view=view,
            state={
                "branch_state": initial_branch_state,
                "selected_preview": None,
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


def _parse_tag_filter(value: str | None) -> list[str]:
    if not isinstance(value, str):
        return []
    return [token.strip() for token in value.split(",") if token.strip()]


def _tag_field_name(tag_id: str) -> str:
    return f"tag__{tag_id}"


async def _resolve_search_inputs(
    *,
    services: AppServices,
    clerk_user_id: str,
    query: str | None,
    data: dict[str, object] | None,
    tag_ids: list[str] | None,
    tag_filter: str | None,
    tag_match_mode: Literal["all", "any"],
) -> tuple[str, list[str], Literal["all", "any"]]:
    if data is not None:
        resolved_query = _form_string(data, "query")
        resolved_tag_ids = sorted(
            field_name.removeprefix("tag__")
            for field_name, value in data.items()
            if field_name.startswith("tag__") and _truthy_form_value(value)
        )
        resolved_tag_match_mode = _form_match_mode(data, default=tag_match_mode)
        return resolved_query, resolved_tag_ids, resolved_tag_match_mode

    if tag_ids is not None:
        return query or "", tag_ids, tag_match_mode

    resolved_tag_ids = await services.file_library.resolve_tag_ids(
        clerk_user_id=clerk_user_id,
        tag_tokens=_parse_tag_filter(tag_filter),
    )
    return query or "", resolved_tag_ids, tag_match_mode


def _truthy_form_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "on", "yes"}
    return False


def _form_string(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def _form_int(data: dict[str, object], key: str, *, default: int) -> int:
    value = data.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        with_value = value.strip()
        if with_value.isdigit():
            return int(with_value)
    return default


def _form_match_mode(
    data: dict[str, object],
    *,
    default: Literal["all", "any"],
) -> Literal["all", "any"]:
    value = data.get("tag_match_mode")
    if value in {"all", "any"}:
        return value
    return default


def _build_search_form_model(
    *,
    name: str,
    available_tags: list[dict[str, Any]],
    query: str,
    selected_tag_ids: list[str],
    tag_match_mode: Literal["all", "any"],
    descend: int | None = None,
    max_width: int | None = None,
) -> type[BaseModel]:
    selected_tag_id_set = set(selected_tag_ids)
    model_fields: dict[str, tuple[Any, Any]] = {
        "query": (
            str,
            Field(
                default=query,
                title="Query",
                description="Describe the files you want to find.",
            ),
        ),
        "tag_match_mode": (
            Literal["all", "any"],
            Field(
                default=tag_match_mode,
                title="Tag Match Mode",
                description="When multiple tags are selected, require all of them or allow any of them.",
            ),
        ),
    }

    if descend is not None:
        model_fields["descend"] = (
            int,
            Field(default=descend, title="Depth", ge=0, le=4),
        )
    if max_width is not None:
        model_fields["max_width"] = (
            int,
            Field(default=max_width, title="Width", ge=1, le=8),
        )

    for tag in available_tags:
        tag_id = str(tag["id"])
        model_fields[_tag_field_name(tag_id)] = (
            bool,
            Field(default=tag_id in selected_tag_id_set, title=str(tag["name"])),
        )

    return create_model(name, **model_fields)


def _build_search_form_arguments(
    *,
    available_tags: list[dict[str, Any]],
    include_branch_controls: bool,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "query": "{{ query }}",
        "tag_match_mode": "{{ tag_match_mode }}",
    }
    if include_branch_controls:
        data["descend"] = "{{ descend }}"
        data["max_width"] = "{{ max_width }}"
    for tag in available_tags:
        field_name = _tag_field_name(str(tag["id"]))
        data[field_name] = "{{ " + field_name + " }}"
    return data
