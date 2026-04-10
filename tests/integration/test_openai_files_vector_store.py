from __future__ import annotations

from collections.abc import Sequence
import subprocess
import uuid
from pathlib import Path
from typing import Any, TypeVar

import pytest
from mcp.types import CallToolResult, ContentBlock
from openai import OpenAI
from pydantic import BaseModel

from apps.openai_files_vector_store.backend.schemas import (
    AskVectorStoreResult,
    AttachFilesResult,
    FileListResult,
    FilePreviewResult,
    OpenVectorStoreConsoleResult,
    SearchVectorStoreResult,
    UploadFileResult,
    VectorStoreListResult,
    VectorStoreStatusResult,
    VectorStoreSummary,
)
from apps.openai_files_vector_store.backend.server import (
    CONSOLE_RESOURCE_URI,
    RESOURCE_MIME_TYPE,
    create_server,
)
from apps.openai_files_vector_store.backend.settings import AppSettings

type ToolCallResponse = Sequence[ContentBlock] | dict[str, Any] | CallToolResult

ResultModelT = TypeVar("ResultModelT", bound=BaseModel)

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = REPO_ROOT / "apps/openai_files_vector_store/ui"
UI_DIST_PATH = UI_DIR / "dist/mcp-app.html"


def test_settings_load_from_dotenv() -> None:
    settings = AppSettings()
    assert settings.openai_api_key.get_secret_value()
    assert settings.openai_agent_model == "gpt-5.4"
    assert settings.openai_file_search_max_results == 5
    assert settings.log_level == "INFO"


@pytest.mark.asyncio
async def test_server_exposes_expected_tools() -> None:
    server = create_server(AppSettings())

    tool_names = {tool.name for tool in await server.list_tools()}

    assert tool_names == {
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
    }


@pytest.fixture(scope="session")
def built_console_ui() -> Path:
    subprocess.run(
        ["npm", "run", "build"],
        check=True,
        cwd=UI_DIR,
    )
    assert UI_DIST_PATH.is_file()
    return UI_DIST_PATH


@pytest.mark.asyncio
async def test_server_exposes_console_resource(
    built_console_ui: Path,
) -> None:
    server = create_server(AppSettings())

    resources = await server.list_resources()
    resource = next(
        resource_item
        for resource_item in resources
        if str(resource_item.uri) == CONSOLE_RESOURCE_URI
    )
    assert resource.mimeType == RESOURCE_MIME_TYPE

    contents = await server.read_resource(CONSOLE_RESOURCE_URI)
    assert len(contents) == 1
    assert contents[0].mime_type == RESOURCE_MIME_TYPE
    content = (
        contents[0].content
        if isinstance(contents[0].content, str)
        else str(contents[0].content, encoding="utf-8")
    )
    assert "<title>OpenAI Files Vector Store Console</title>" in content
    assert "vector-store-console-root" in content


@pytest.mark.asyncio
async def test_live_upload_attach_search_and_ask(
    tmp_path: Path,
) -> None:
    settings = AppSettings()
    server = create_server(settings)
    cleanup_client = OpenAI(api_key=settings.openai_api_key.get_secret_value())

    marker = f"nebula-lighthouse-{uuid.uuid4().hex[:8]}"
    local_file = tmp_path / "facts.txt"
    local_file.write_text(
        "\n".join(
            [
                "This file exists for the MCP integration test.",
                f"The retrieval marker is {marker}.",
                "Use that exact marker when answering questions.",
            ]
        ),
        encoding="utf-8",
    )

    vector_store_id: str | None = None
    file_id: str | None = None
    try:
        create_result = _structured_result(
            await server.call_tool(
                "create_vector_store",
                {
                    "name": f"VS Code MCP Test {marker}",
                    "metadata": {"test_case": marker},
                },
            ),
            VectorStoreSummary,
        )
        vector_store_id = create_result.id

        upload_result = _structured_result(
            await server.call_tool(
                "upload_file",
                {
                    "local_path": str(local_file),
                },
            ),
            UploadFileResult,
        )
        file_id = upload_result.uploaded_file.id

        file_list_result = _structured_result(
            await server.call_tool("list_files", {"limit": 50}),
            FileListResult,
        )
        assert any(file_entry.id == file_id for file_entry in file_list_result.files)

        attach_result = _structured_result(
            await server.call_tool(
                "attach_files_to_vector_store",
                {
                    "vector_store_id": vector_store_id,
                    "file_ids": [file_id],
                },
            ),
            AttachFilesResult,
        )
        assert attach_result.attached_files[0].status == "completed"

        console_result = _structured_result(
            await server.call_tool(
                "open_vector_store_console",
                {
                    "vector_store_id": vector_store_id,
                },
            ),
            OpenVectorStoreConsoleResult,
        )
        assert console_result.selected_vector_store_id == vector_store_id
        assert console_result.selected_vector_store_status is not None
        assert (
            console_result.selected_vector_store_status.vector_store.id
            == vector_store_id
        )
        assert console_result.search_panel.query == ""
        assert console_result.ask_panel.question == ""
        assert any(
            vector_store.id == vector_store_id
            for vector_store in console_result.vector_store_list.vector_stores
        )

        vector_store_list_result = _structured_result(
            await server.call_tool("list_vector_stores", {"limit": 50}),
            VectorStoreListResult,
        )
        assert any(
            vector_store.id == vector_store_id
            for vector_store in vector_store_list_result.vector_stores
        )

        status_result = _structured_result(
            await server.call_tool(
                "get_vector_store_status",
                {
                    "vector_store_id": vector_store_id,
                },
            ),
            VectorStoreStatusResult,
        )
        assert status_result.vector_store.status in {"completed", "in_progress"}
        assert any(vector_file.id == file_id for vector_file in status_result.files)

        preview_result = _structured_result(
            await server.call_tool(
                "preview_file",
                {
                    "vector_store_id": vector_store_id,
                    "file_id": file_id,
                },
            ),
            FilePreviewResult,
        )
        assert preview_result.vector_store_id == vector_store_id
        assert preview_result.file_id == file_id
        assert preview_result.filename == local_file.name
        assert preview_result.preview_text is not None
        assert marker in preview_result.preview_text

        search_result = _structured_result(
            await server.call_tool(
                "search_vector_store",
                {
                    "vector_store_id": vector_store_id,
                    "query": marker,
                    "max_num_results": 5,
                },
            ),
            SearchVectorStoreResult,
        )
        assert search_result.total_hits >= 1
        assert any(marker in hit.text for hit in search_result.hits)

        ask_result = _structured_result(
            await server.call_tool(
                "ask_vector_store",
                {
                    "vector_store_id": vector_store_id,
                    "question": "What is the retrieval marker in the indexed document?",
                    "max_num_results": 5,
                },
            ),
            AskVectorStoreResult,
        )
        assert marker in ask_result.answer
        assert ask_result.search_calls
        assert any(
            marker in result.text
            for search_call in ask_result.search_calls
            for result in search_call.results
        )
    finally:
        if vector_store_id is not None:
            cleanup_client.vector_stores.delete(vector_store_id)
        if file_id is not None:
            cleanup_client.files.delete(file_id)


def _structured_result(
    result: ToolCallResponse,
    result_type: type[ResultModelT],
) -> ResultModelT:
    if isinstance(result, CallToolResult):
        structured_content = result.structuredContent
    else:
        structured_content = result

    assert structured_content is not None
    assert isinstance(structured_content, dict)
    return result_type.model_validate(structured_content)
