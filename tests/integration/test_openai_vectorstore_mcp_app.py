from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from chatkit.server import NonStreamingResult

from apps.openai_vectorstore_mcp_app.backend.clerk import (
    ClerkUserRecord,
    ClerkVerifiedSessionToken,
)
from apps.openai_vectorstore_mcp_app.backend.db import DatabaseManager
from apps.openai_vectorstore_mcp_app.backend.models import (
    AppUser,
    DerivedArtifact,
    KnowledgeBase,
    KnowledgeNode,
    KnowledgeNodeTag,
    KnowledgeTag,
)
from apps.openai_vectorstore_mcp_app.backend.server import (
    create_fastapi_app,
    create_mcp_server,
    create_services,
)
from apps.openai_vectorstore_mcp_app.backend.settings import AppSettings


@pytest.fixture
def configured_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AppSettings:
    static_dir = tmp_path / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "index.html").write_text(
        "<!doctype html><html><body><div id='root'>File Desk</div></body></html>",
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("CLERK_SECRET_KEY", "test-clerk-secret")
    monkeypatch.setenv("APP_SIGNING_SECRET", "test-signing-secret")
    monkeypatch.setenv("CLERK_ISSUER_URL", "https://clerk.example.com")
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{tmp_path / 'file-desk.db'}",
    )
    monkeypatch.setenv("STATIC_DIR", str(static_dir))
    return AppSettings()


def test_settings_load_from_env(configured_settings: AppSettings) -> None:
    assert configured_settings.openai_api_key.get_secret_value() == "test-openai-key"
    assert configured_settings.clerk_secret_key.get_secret_value() == "test-clerk-secret"
    assert configured_settings.app_signing_secret.get_secret_value() == "test-signing-secret"
    assert configured_settings.openai_agent_model == "gpt-5.4-mini"
    assert configured_settings.mcp_required_scopes == ["openid", "email", "profile"]
    assert configured_settings.normalized_static_dir


@pytest.mark.asyncio
async def test_mcp_server_exposes_file_desk_tools(configured_settings: AppSettings) -> None:
    services = create_services(configured_settings)
    server = create_mcp_server(configured_settings, services)
    try:
        tools = {tool.name: tool for tool in await server.list_tools(run_middleware=False)}
    finally:
        await services.close()

    assert set(tools) == {
        "list_files",
        "list_tags",
        "search_files",
        "get_file_details",
        "read_file_text",
        "delete_file",
        "open_file_library",
    }
    assert tools["open_file_library"].meta is not None
    assert tools["open_file_library"].meta["ui"]["resourceUri"].startswith("ui://")


@pytest.mark.asyncio
async def test_fastapi_routes_cover_health_static_files_and_chat(
    configured_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_file_library(configured_settings)

    async def verify_session_token(_self, token: str):
        if token != "test-session":
            return None
        return ClerkVerifiedSessionToken(
            subject="user_123",
            session_id="sess_123",
            token_id="tok_123",
            expiration=None,
        )

    async def get_user_record(_self, clerk_user_id: str) -> ClerkUserRecord:
        assert clerk_user_id == "user_123"
        return ClerkUserRecord(
            clerk_user_id="user_123",
            primary_email="owner@example.com",
            display_name="File Desk Owner",
            active=True,
            role="admin",
        )

    async def delete_file_noop(_self, *, file_id: str) -> None:
        return None

    async def fake_chat_process(_self, request: str | bytes | bytearray, context):
        assert context.clerk_user_id == "user_123"
        assert request
        return NonStreamingResult(b'{"ok":true}')

    monkeypatch.setattr(
        "apps.openai_vectorstore_mcp_app.backend.clerk.ClerkAuthService.verify_session_token",
        verify_session_token,
    )
    monkeypatch.setattr(
        "apps.openai_vectorstore_mcp_app.backend.clerk.ClerkAuthService.get_user_record",
        get_user_record,
    )
    monkeypatch.setattr(
        "apps.openai_vectorstore_mcp_app.backend.openai_gateway.OpenAIKnowledgeBaseGateway.delete_file",
        delete_file_noop,
    )
    monkeypatch.setattr(
        "apps.openai_vectorstore_mcp_app.backend.chatkit_server.FileDeskChatKitServer.process",
        fake_chat_process,
    )

    app = create_fastapi_app(configured_settings)
    headers = {"Authorization": "Bearer test-session"}

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}

            root = await client.get("/")
            assert root.status_code == 200
            assert "File Desk" in root.text

            files_response = await client.get("/api/files", headers=headers)
            assert files_response.status_code == 200
            payload = files_response.json()
            assert payload["total_count"] == 1
            assert payload["files"][0]["display_title"] == "Alpha Notes"

            detail_response = await client.get("/api/files/node_alpha", headers=headers)
            assert detail_response.status_code == 200
            assert detail_response.json()["derived_artifacts"][0]["kind"] == "document_text"

            tags_response = await client.get("/api/tags", headers=headers)
            assert tags_response.status_code == 200
            assert tags_response.json()["tags"][0]["name"] == "Operations"

            chat_response = await client.post(
                "/api/chatkit",
                headers=headers,
                content=b'{"type":"threads.create","params":{"input":{"content":[],"attachments":[],"inference_options":{}}},"metadata":{"selected_file_ids":["node_alpha"]}}',
            )
            assert chat_response.status_code == 200
            assert chat_response.json() == {"ok": True}

            delete_response = await client.delete("/api/files/node_alpha", headers=headers)
            assert delete_response.status_code == 200
            assert delete_response.json() == {"deleted_file_id": "node_alpha"}

            files_after_delete = await client.get("/api/files", headers=headers)
            assert files_after_delete.status_code == 200
            assert files_after_delete.json()["total_count"] == 0


async def _seed_file_library(settings: AppSettings) -> None:
    database = DatabaseManager(settings)
    await database.ensure_ready()
    async with database.session() as session:
        app_user = AppUser(
            clerk_user_id="user_123",
            primary_email="owner@example.com",
            display_name="File Desk Owner",
            active=True,
            role="admin",
            last_seen_at=datetime.now(UTC),
        )
        session.add(app_user)
        await session.flush()

        knowledge_base = KnowledgeBase(
            id="kb_alpha",
            user_id=app_user.id,
            title="Owner Library",
            description="Personal file desk",
            openai_vector_store_id="vs_alpha",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(knowledge_base)
        await session.flush()

        tag = KnowledgeTag(
            id="tag_ops",
            knowledge_base_id=knowledge_base.id,
            name="Operations",
            slug="operations",
            color="#c46a32",
            created_at=datetime.now(UTC),
        )
        session.add(tag)

        node = KnowledgeNode(
            id="node_alpha",
            knowledge_base_id=knowledge_base.id,
            created_by_user_id=app_user.id,
            display_title="Alpha Notes",
            original_filename="alpha-notes.txt",
            media_type="text/plain",
            source_kind="document",
            status="ready",
            byte_size=128,
            original_mime_type="text/plain",
            openai_original_file_id="file_alpha",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(node)
        await session.flush()

        session.add(
            KnowledgeNodeTag(
                node_id=node.id,
                tag_id=tag.id,
            )
        )
        session.add(
            DerivedArtifact(
                id="artifact_alpha",
                node_id=node.id,
                kind="document_text",
                openai_file_id="artifact_file_alpha",
                text_content="Alpha notes explain how the file desk should work.",
                structured_payload=None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
    await database.close()
