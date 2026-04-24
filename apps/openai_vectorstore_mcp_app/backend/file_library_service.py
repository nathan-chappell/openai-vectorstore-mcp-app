from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .clerk import ClerkAuthService
from .db import DatabaseManager
from .knowledge_base_service import KnowledgeBaseService, ResolvedUser
from .models import KnowledgeBase, KnowledgeNode, KnowledgeTag
from .openai_gateway import OpenAIKnowledgeBaseGateway, build_filter_groups
from .schemas import (
    KnowledgeNodeDetail,
    KnowledgeNodeSummary,
    KnowledgeTagSummary,
    SearchHit,
    TagMatchMode,
    UploadFinalizeResult,
    UserSummary,
)
from .upload_sessions import KnowledgeBaseSessionService, UploadSessionClaims


class FileListResponse(BaseModel):
    files: list[KnowledgeNodeSummary] = Field(default_factory=list)
    total_count: int
    page: int
    page_size: int
    has_more: bool


class TagListResponse(BaseModel):
    tags: list[KnowledgeTagSummary] = Field(default_factory=list)


class DeleteFileResult(BaseModel):
    deleted_file_id: str


class FileLibraryService:
    """File-centric facade over the existing knowledge-base storage model."""

    def __init__(
        self,
        *,
        database: DatabaseManager,
        clerk_auth: ClerkAuthService,
        session_tokens: KnowledgeBaseSessionService,
        openai_gateway: OpenAIKnowledgeBaseGateway,
        legacy_service: KnowledgeBaseService,
    ) -> None:
        self._database = database
        self._clerk_auth = clerk_auth
        self._session_tokens = session_tokens
        self._openai_gateway = openai_gateway
        self._legacy = legacy_service

    async def issue_upload_session(self, *, clerk_user_id: str):
        await self._database.ensure_ready()
        async with self._database.session() as session:
            resolved_user = await self._resolved_user(session, clerk_user_id=clerk_user_id)
            self._legacy._require_active(resolved_user)
            knowledge_base = await self._legacy._knowledge_base_for_user(
                session,
                resolved_user=resolved_user,
            )
            return self._session_tokens.issue_upload_session(
                clerk_user_id=clerk_user_id,
                knowledge_base_id=knowledge_base.id,
            )

    async def ingest_file_for_user(
        self,
        *,
        clerk_user_id: str,
        local_path: Path,
        filename: str,
        declared_media_type: str | None,
        tag_ids: list[str],
    ) -> UploadFinalizeResult:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            resolved_user = await self._resolved_user(session, clerk_user_id=clerk_user_id)
            self._legacy._require_active(resolved_user)
            knowledge_base = await self._legacy._knowledge_base_for_user(
                session,
                resolved_user=resolved_user,
            )
            claims = UploadSessionClaims(
                clerk_user_id=clerk_user_id,
                knowledge_base_id=knowledge_base.id,
            )
        return await self._legacy.ingest_upload(
            claims=claims,
            local_path=local_path,
            filename=filename,
            declared_media_type=declared_media_type,
            tag_ids=tag_ids,
        )

    async def list_files(
        self,
        *,
        clerk_user_id: str,
        query: str | None,
        tag_ids: list[str],
        tag_match_mode: TagMatchMode,
        page: int,
        page_size: int,
    ) -> FileListResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            resolved_user = await self._resolved_user(session, clerk_user_id=clerk_user_id)
            self._legacy._require_active(resolved_user)
            knowledge_base = await self._legacy._knowledge_base_for_user(
                session,
                resolved_user=resolved_user,
            )
            selected_tags = await self._legacy._knowledge_tags_by_ids(
                session,
                knowledge_base_id=knowledge_base.id,
                tag_ids=tag_ids,
            )
            matching_nodes = self._filter_nodes(
                knowledge_base=knowledge_base,
                query=query,
                selected_tags=selected_tags,
                tag_match_mode=tag_match_mode,
            )
            start = max(page - 1, 0) * page_size
            end = start + page_size
            page_nodes = matching_nodes[start:end]
            return FileListResponse(
                files=[
                    await self._legacy._node_summary(
                        session,
                        knowledge_base=knowledge_base,
                        node=node,
                        clerk_user_id=clerk_user_id,
                    )
                    for node in page_nodes
                ],
                total_count=len(matching_nodes),
                page=page,
                page_size=page_size,
                has_more=end < len(matching_nodes),
            )

    async def list_tags(
        self,
        *,
        clerk_user_id: str,
    ) -> TagListResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            resolved_user = await self._resolved_user(session, clerk_user_id=clerk_user_id)
            self._legacy._require_active(resolved_user)
            knowledge_base = await self._legacy._knowledge_base_for_user(
                session,
                resolved_user=resolved_user,
            )
            return TagListResponse(
                tags=[
                    self._legacy._tag_summary(tag, node_count=self._legacy._tag_node_count(tag))
                    for tag in sorted(knowledge_base.tags, key=lambda item: item.name.lower())
                ]
            )

    async def get_file_detail(
        self,
        *,
        clerk_user_id: str,
        file_id: str,
    ) -> KnowledgeNodeDetail:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            resolved_user = await self._resolved_user(session, clerk_user_id=clerk_user_id)
            self._legacy._require_active(resolved_user)
            knowledge_base = await self._legacy._knowledge_base_for_user(
                session,
                resolved_user=resolved_user,
            )
            node = await self._legacy._node_for_user(
                session,
                resolved_user=resolved_user,
                node_id=file_id,
            )
            return await self._legacy._node_detail(
                session,
                knowledge_base=knowledge_base,
                node=node,
                clerk_user_id=clerk_user_id,
            )

    async def read_file_text(
        self,
        *,
        clerk_user_id: str,
        file_id: str,
        max_chars: int = 12_000,
    ) -> str:
        detail = await self.get_file_detail(clerk_user_id=clerk_user_id, file_id=file_id)
        if detail.derived_artifacts:
            text = "\n\n".join(
                artifact.text_content.strip() for artifact in detail.derived_artifacts if artifact.text_content.strip()
            ).strip()
            if text:
                return text[:max_chars]
        if detail.media_type.startswith("text/") and detail.download_url:
            return f"Text preview unavailable for {detail.display_title}."
        return f"No extracted text is available for {detail.display_title}."

    async def search_files(
        self,
        *,
        clerk_user_id: str,
        query: str,
        tag_ids: list[str],
        tag_match_mode: TagMatchMode,
        max_results: int,
    ) -> list[SearchHit]:
        normalized_query = query.strip()
        if not normalized_query:
            return []

        await self._database.ensure_ready()
        async with self._database.session() as session:
            resolved_user = await self._resolved_user(session, clerk_user_id=clerk_user_id)
            self._legacy._require_active(resolved_user)
            knowledge_base = await self._legacy._knowledge_base_for_user(
                session,
                resolved_user=resolved_user,
            )
            if knowledge_base.openai_vector_store_id is None:
                return []

            selected_tags = await self._legacy._knowledge_tags_by_ids(
                session,
                knowledge_base_id=knowledge_base.id,
                tag_ids=tag_ids,
            )
            matching_nodes = self._filter_nodes(
                knowledge_base=knowledge_base,
                query=None,
                selected_tags=selected_tags,
                tag_match_mode=tag_match_mode,
            )
            all_node_ids = {node.id for node in knowledge_base.nodes}
            scoped_node_ids = [node.id for node in matching_nodes]
            tag_slugs = [tag.slug for tag in selected_tags]
            filters = build_filter_groups(
                node_ids=[] if set(scoped_node_ids) == all_node_ids else scoped_node_ids,
                media_types=[],
                tag_slugs=tag_slugs,
                tag_match_mode=tag_match_mode,
            )
            return await self._openai_gateway.search_vector_store(
                vector_store_id=knowledge_base.openai_vector_store_id,
                query=normalized_query,
                max_results=max_results,
                rewrite_query=True,
                filters=filters,
            )

    async def delete_file(
        self,
        *,
        clerk_user_id: str,
        file_id: str,
    ) -> DeleteFileResult:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            resolved_user = await self._resolved_user(session, clerk_user_id=clerk_user_id)
            self._legacy._require_active(resolved_user)
            knowledge_base = await self._legacy._knowledge_base_for_user(
                session,
                resolved_user=resolved_user,
            )
            node = await self._legacy._node_for_user(
                session,
                resolved_user=resolved_user,
                node_id=file_id,
            )
            file_ids = {
                candidate
                for candidate in [
                    node.openai_original_file_id,
                    *[
                        artifact.openai_file_id
                        for artifact in node.derived_artifacts
                        if artifact.openai_file_id is not None
                    ],
                ]
                if candidate is not None
            }
            for candidate in sorted(file_ids):
                await self._openai_gateway.delete_file(file_id=candidate)
            knowledge_base.updated_at = datetime.now(UTC)
            await session.delete(node)
            await session.commit()
            return DeleteFileResult(deleted_file_id=file_id)

    async def _resolved_user(self, session, *, clerk_user_id: str) -> ResolvedUser:
        if clerk_user_id == "local-dev":
            app_user = await self._legacy._ensure_local_dev_user(session)
            return ResolvedUser(
                app_user=app_user,
                summary=UserSummary(
                    clerk_user_id=app_user.clerk_user_id,
                    display_name=app_user.display_name or "Local Developer",
                    primary_email=app_user.primary_email,
                    active=app_user.active,
                    role=app_user.role,
                ),
            )
        clerk_record = await self._clerk_auth.get_user_record(clerk_user_id)
        app_user = await self._legacy._upsert_clerk_user(session, clerk_record)
        return ResolvedUser(
            app_user=app_user,
            summary=UserSummary(
                clerk_user_id=clerk_record.clerk_user_id,
                display_name=clerk_record.display_name,
                primary_email=clerk_record.primary_email,
                active=clerk_record.active,
                role=clerk_record.role,
            ),
        )

    def _filter_nodes(
        self,
        *,
        knowledge_base: KnowledgeBase,
        query: str | None,
        selected_tags: list[KnowledgeTag],
        tag_match_mode: TagMatchMode,
    ) -> list[KnowledgeNode]:
        normalized_query = query.strip().lower() if isinstance(query, str) and query.strip() else None
        selected_tag_ids = {tag.id for tag in selected_tags}
        matching_nodes: list[KnowledgeNode] = []
        for node in sorted(
            knowledge_base.nodes,
            key=lambda item: (item.updated_at, item.created_at),
            reverse=True,
        ):
            node_tag_ids = {link.tag_id for link in node.tag_links}
            if selected_tag_ids:
                if tag_match_mode == "all" and not selected_tag_ids.issubset(node_tag_ids):
                    continue
                if tag_match_mode == "any" and not selected_tag_ids.intersection(node_tag_ids):
                    continue
            if normalized_query is not None and not self._matches_query(node, normalized_query):
                continue
            matching_nodes.append(node)
        return matching_nodes

    @staticmethod
    def _matches_query(node: KnowledgeNode, query: str) -> bool:
        fields = [
            node.display_title,
            node.original_filename,
            node.media_type,
            node.source_kind,
            *[link.tag.name for link in node.tag_links],
            *[artifact.text_content[:2_000] for artifact in node.derived_artifacts],
        ]
        haystack = "\n".join(field for field in fields if field).lower()
        return query in haystack
