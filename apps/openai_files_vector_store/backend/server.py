from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from openai.types.file_purpose import FilePurpose
from pydantic import BaseModel, Field

from .logging import configure_logging
from .openai_gateway import OpenAIFilesVectorStoreGateway
from .qa_agent import VectorStoreQuestionAnswerer
from .schemas import (
    AskPanelState,
    FilePreviewResult,
    OpenVectorStoreConsoleResult,
    SearchPanelState,
    ToolAttributes,
    VectorStoreMetadata,
    VectorStoreListResult,
)
from .settings import AppSettings, get_settings

logger = logging.getLogger(__name__)

RESOURCE_MIME_TYPE = "text/html;profile=mcp-app"
CONSOLE_RESOURCE_URI = "ui://openai-files-vector-store/console.html"
CONSOLE_UI_PATH = (
    Path(__file__).resolve().parent.parent / "ui" / "dist" / "mcp-app.html"
)
DEFAULT_FILE_PREVIEW_MAX_CHARS = 32_768


def create_server(settings: AppSettings | None = None) -> FastMCP:
    """Create the FastMCP server for OpenAI Files + Vector Store operations."""

    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    gateway = OpenAIFilesVectorStoreGateway(resolved_settings)
    question_answerer = VectorStoreQuestionAnswerer(resolved_settings)

    server = FastMCP(
        name=resolved_settings.app_name,
        instructions=(
            "Manage OpenAI Files and Vector Stores for retrieval-oriented agent workflows. "
            "Use the tools to upload files, attach them to stores, inspect ingestion state, "
            "search raw chunks, and ask grounded questions over a selected store."
        ),
        log_level=resolved_settings.log_level,
    )

    @server.tool(
        name="open_vector_store_console",
        title="Open Vector Store Console",
        description=(
            "Open the interactive vector store console and seed it with the current "
            "vector store list, selected store status, and empty retrieval panels."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        meta={"ui": {"resourceUri": CONSOLE_RESOURCE_URI}},
    )
    def open_vector_store_console(
        vector_store_id: Annotated[str | None, Field(min_length=1)] = None,
    ) -> CallToolResult:
        vector_store_list = gateway.list_vector_stores(limit=20)
        selected_vector_store_id = vector_store_id
        if selected_vector_store_id is None and vector_store_list.vector_stores:
            selected_vector_store_id = vector_store_list.vector_stores[0].id

        selected_vector_store_status = None
        if selected_vector_store_id is not None:
            selected_vector_store_status = gateway.get_vector_store_status(
                vector_store_id=selected_vector_store_id,
                file_limit=20,
                batch_id=None,
            )
            if not any(
                vector_store.id == selected_vector_store_id
                for vector_store in vector_store_list.vector_stores
            ):
                vector_store_list = VectorStoreListResult(
                    vector_stores=[
                        selected_vector_store_status.vector_store,
                        *vector_store_list.vector_stores,
                    ],
                    total_returned=len(vector_store_list.vector_stores) + 1,
                )

        payload = OpenVectorStoreConsoleResult(
            vector_store_list=vector_store_list,
            selected_vector_store_id=selected_vector_store_id,
            selected_vector_store_status=selected_vector_store_status,
            search_panel=SearchPanelState(
                max_num_results=resolved_settings.openai_file_search_max_results,
            ),
            ask_panel=AskPanelState(
                max_num_results=resolved_settings.openai_file_search_max_results,
            ),
        )
        if selected_vector_store_id is None:
            summary = "Opened vector store console with no vector stores yet."
        else:
            summary = (
                f"Opened vector store console for {selected_vector_store_id} with "
                f"{payload.vector_store_list.total_returned} available store(s)."
            )
        return _tool_result(
            summary,
            payload,
            meta={"ui": {"resourceUri": CONSOLE_RESOURCE_URI}},
        )

    @server.tool(
        name="upload_file",
        title="Upload File",
        description=(
            "Upload a local file to OpenAI Files and optionally attach it to a vector store "
            "in the same operation."
        ),
        annotations=ToolAnnotations(
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    def upload_file(
        local_path: Annotated[str, Field(min_length=1)],
        vector_store_id: Annotated[str | None, Field(min_length=1)] = None,
        purpose: FilePurpose = "assistants",
        attributes: ToolAttributes | None = None,
    ) -> CallToolResult:
        payload = gateway.upload_file(
            local_path=local_path,
            vector_store_id=vector_store_id,
            purpose=purpose,
            attributes=attributes,
        )
        summary = (
            f"Uploaded {payload.uploaded_file.filename} as {payload.uploaded_file.id}."
        )
        if payload.attached_file is not None:
            summary += (
                f" Attached to vector store {payload.vector_store_id} with status "
                f"{payload.attached_file.status}."
            )
        return _tool_result(summary, payload)

    @server.tool(
        name="list_files",
        title="List Files",
        description="List recent OpenAI files and optionally filter them by purpose.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    def list_files(
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        purpose: str | None = None,
    ) -> CallToolResult:
        payload = gateway.list_files(limit=limit, purpose=purpose)
        summary = f"Returned {payload.total_returned} file(s)."
        return _tool_result(summary, payload)

    @server.tool(
        name="preview_file",
        title="Preview File",
        description=(
            "Fetch a text preview for a single OpenAI file so the MCP app can "
            "inspect listed files inline."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        meta={"ui": {"visibility": ["app"]}},
    )
    def preview_file(
        vector_store_id: Annotated[str, Field(min_length=1)],
        file_id: Annotated[str, Field(min_length=1)],
        max_chars: Annotated[int | None, Field(ge=1, le=131_072)] = None,
    ) -> CallToolResult:
        payload = gateway.preview_file(
            file_id=file_id,
            vector_store_id=vector_store_id,
            max_chars=max_chars or DEFAULT_FILE_PREVIEW_MAX_CHARS,
        )
        if payload.preview_text is None:
            summary = f"Loaded metadata for {payload.filename}; inline preview is unavailable."
        elif payload.preview_truncated:
            summary = f"Loaded a truncated preview for {payload.filename}."
        else:
            summary = f"Loaded a preview for {payload.filename}."
        return _tool_result(summary, payload)

    @server.tool(
        name="create_vector_store",
        title="Create Vector Store",
        description="Create a new OpenAI vector store for retrieval workloads.",
        annotations=ToolAnnotations(
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    def create_vector_store(
        name: Annotated[str | None, Field(min_length=1)] = None,
        description: Annotated[str | None, Field(min_length=1)] = None,
        metadata: VectorStoreMetadata | None = None,
    ) -> CallToolResult:
        payload = gateway.create_vector_store(
            name=name,
            description=description,
            metadata=metadata,
        )
        summary = f"Created vector store {payload.id} with status {payload.status}."
        return _tool_result(summary, payload)

    @server.tool(
        name="list_vector_stores",
        title="List Vector Stores",
        description="List recent OpenAI vector stores and their ingestion summaries.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    def list_vector_stores(
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> CallToolResult:
        payload = gateway.list_vector_stores(limit=limit)
        summary = f"Returned {payload.total_returned} vector store(s)."
        return _tool_result(summary, payload)

    @server.tool(
        name="attach_files_to_vector_store",
        title="Attach Files To Vector Store",
        description=(
            "Attach existing OpenAI file IDs and/or local files to a vector store. "
            "When more than one file is involved, the tool uses batch ingestion."
        ),
        annotations=ToolAnnotations(
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    def attach_files_to_vector_store(
        vector_store_id: Annotated[str, Field(min_length=1)],
        file_ids: list[str] | None = None,
        local_paths: list[str] | None = None,
        attributes: ToolAttributes | None = None,
    ) -> CallToolResult:
        payload = gateway.attach_files_to_vector_store(
            vector_store_id=vector_store_id,
            file_ids=file_ids,
            local_paths=local_paths,
            attributes=attributes,
        )
        summary = f"Attached {len(payload.attached_files)} file(s) to vector store {vector_store_id}."
        if payload.batch is not None:
            summary += f" Batch {payload.batch.id} finished with status {payload.batch.status}."
        return _tool_result(summary, payload)

    @server.tool(
        name="get_vector_store_status",
        title="Get Vector Store Status",
        description=(
            "Retrieve vector store details, attached file states, and optional batch state "
            "for a known batch ID."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    def get_vector_store_status(
        vector_store_id: Annotated[str, Field(min_length=1)],
        file_limit: Annotated[int, Field(ge=1, le=100)] = 20,
        batch_id: Annotated[str | None, Field(min_length=1)] = None,
    ) -> CallToolResult:
        payload = gateway.get_vector_store_status(
            vector_store_id=vector_store_id,
            file_limit=file_limit,
            batch_id=batch_id,
        )
        summary = (
            f"Vector store {payload.vector_store.id} is {payload.vector_store.status} with "
            f"{payload.vector_store.file_counts.completed}/{payload.vector_store.file_counts.total} "
            "completed file(s)."
        )
        return _tool_result(summary, payload)

    @server.tool(
        name="search_vector_store",
        title="Search Vector Store",
        description="Run a direct vector store search and return raw matching chunks.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    def search_vector_store(
        vector_store_id: Annotated[str, Field(min_length=1)],
        query: Annotated[str, Field(min_length=1)],
        max_num_results: Annotated[int | None, Field(ge=1)] = None,
        rewrite_query: bool = False,
    ) -> CallToolResult:
        payload = gateway.search_vector_store(
            vector_store_id=vector_store_id,
            query=query,
            max_num_results=max_num_results
            or resolved_settings.openai_file_search_max_results,
            rewrite_query=rewrite_query,
        )
        summary = f"Found {payload.total_hits} hit(s) for query '{query}'."
        return _tool_result(summary, payload)

    @server.tool(
        name="ask_vector_store",
        title="Ask Vector Store",
        description=(
            "Use openai-agents with hosted file_search to answer a question over a selected "
            "vector store and include the supporting search hits."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def ask_vector_store(
        vector_store_id: Annotated[str, Field(min_length=1)],
        question: Annotated[str, Field(min_length=1)],
        max_num_results: Annotated[int | None, Field(ge=1)] = None,
    ) -> CallToolResult:
        payload = await question_answerer.ask(
            vector_store_id=vector_store_id,
            question=question,
            max_num_results=max_num_results,
        )
        return _tool_result(payload.answer, payload)

    @server.resource(
        CONSOLE_RESOURCE_URI,
        name="open_vector_store_console_resource",
        title="OpenAI Files Vector Store Console",
        description=(
            "Single-file React UI for browsing vector stores, inspecting ingestion "
            "status, and running retrieval workflows."
        ),
        mime_type=RESOURCE_MIME_TYPE,
    )
    def open_vector_store_console_resource() -> str:
        if not CONSOLE_UI_PATH.is_file():
            logger.warning(
                "mcp_app_resource_missing uri=%s path=%s",
                CONSOLE_RESOURCE_URI,
                CONSOLE_UI_PATH,
            )
            return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Vector Store Console Build Required</title>
</head>
<body>
  <main style="font-family: sans-serif; padding: 24px;">
    <h1>Vector Store Console UI not built yet</h1>
    <p>Run <code>npm install</code> and <code>npm run build:watch</code> in <code>apps/openai_files_vector_store/ui</code>, then reopen the tool.</p>
  </main>
</body>
</html>
"""

        html = CONSOLE_UI_PATH.read_text(encoding="utf-8")
        logger.info(
            "mcp_app_resource_ready uri=%s bytes=%s path=%s",
            CONSOLE_RESOURCE_URI,
            len(html),
            CONSOLE_UI_PATH,
        )
        return html

    logger.info(
        "mcp_server_ready name=%s tools=%s",
        resolved_settings.app_name,
        [
            "open_vector_store_console",
            "upload_file",
            "list_files",
            "preview_file",
            "create_vector_store",
            "list_vector_stores",
            "attach_files_to_vector_store",
            "get_vector_store_status",
            "search_vector_store",
            "ask_vector_store",
        ],
    )
    return server


def _tool_result(
    summary: str,
    payload: BaseModel,
    *,
    meta: dict[str, object] | None = None,
) -> CallToolResult:
    return CallToolResult(
        _meta=meta,
        content=[TextContent(type="text", text=summary)],
        structuredContent=payload.model_dump(mode="json"),
    )
