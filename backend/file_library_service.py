from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from pathlib import Path
import re

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .clerk import ClerkAuthService, ClerkUserRecord
from .db import DatabaseManager
from .file_library_gateway import (
    OpenAIFileLibraryGateway,
    build_filter_groups,
    build_searchable_attributes,
    guess_media_type,
)
from .models import AppUser, DerivedArtifact, FileLibrary, FileTag, FileTagLink, LibraryFile
from .schemas import (
    DeleteFileResult,
    DerivedArtifactSummary,
    FileDetail,
    FileListResponse,
    SearchBranchLevel,
    SearchBranchResponse,
    FileSummary,
    FileTagSummary,
    SearchHit,
    TagListResponse,
    TagMatchMode,
    UploadFinalizeResult,
    UploadSessionResult,
    UserSummary,
)
from .session_tokens import FileDownloadClaims, FileLibrarySessionService

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {
    ".c",
    ".cpp",
    ".css",
    ".csv",
    ".go",
    ".html",
    ".htm",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".markdown",
    ".py",
    ".rb",
    ".rs",
    ".rst",
    ".scss",
    ".sh",
    ".sql",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


@dataclass(slots=True)
class ResolvedUser:
    app_user: AppUser
    summary: UserSummary


class FileLibraryService:
    """Own the canonical file-library domain used by both web and MCP surfaces."""

    def __init__(
        self,
        *,
        database: DatabaseManager,
        clerk_auth: ClerkAuthService,
        session_tokens: FileLibrarySessionService,
        openai_gateway: OpenAIFileLibraryGateway,
    ) -> None:
        self._database = database
        self._clerk_auth = clerk_auth
        self._session_tokens = session_tokens
        self._openai_gateway = openai_gateway

    async def ensure_app_user(self, session, *, clerk_user_id: str) -> AppUser:
        if clerk_user_id == "local-dev":
            return await self._ensure_local_dev_user(session)
        existing = await self._user_by_clerk_id(session, clerk_user_id)
        if existing is not None:
            return existing
        clerk_record = await self._clerk_auth.get_user_record(clerk_user_id)
        return await self._upsert_clerk_user(session, clerk_record)

    async def issue_upload_session(self, *, clerk_user_id: str) -> UploadSessionResult:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            resolved_user = await self._resolved_user(session, clerk_user_id=clerk_user_id)
            self._require_active(resolved_user)
            file_library = await self._file_library_for_user(session, resolved_user=resolved_user)
            return self._session_tokens.issue_upload_session(
                clerk_user_id=clerk_user_id,
                file_library_id=file_library.id,
            )

    async def ingest_file_with_upload_token(
        self,
        *,
        clerk_user_id: str,
        upload_token: str,
        local_path: Path,
        filename: str,
        declared_media_type: str | None,
        tag_ids: list[str],
    ) -> UploadFinalizeResult:
        claims = self._session_tokens.verify_upload_session(upload_token)
        if claims is None:
            raise ValueError("Invalid upload token.")
        if claims.clerk_user_id != clerk_user_id:
            raise PermissionError("Upload token does not belong to this user.")

        await self._database.ensure_ready()
        async with self._database.session() as session:
            resolved_user = await self._resolved_user(session, clerk_user_id=clerk_user_id)
            self._require_active(resolved_user)
            file_library = await self._file_library_for_user(session, resolved_user=resolved_user)
            if file_library.id != claims.file_library_id:
                raise PermissionError("Upload token does not match the active file library.")
            return await self._ingest_file(
                session,
                resolved_user=resolved_user,
                file_library=file_library,
                local_path=local_path,
                filename=filename,
                declared_media_type=declared_media_type,
                tag_ids=tag_ids,
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
            self._require_active(resolved_user)
            file_library = await self._file_library_for_user(session, resolved_user=resolved_user)
            return await self._ingest_file(
                session,
                resolved_user=resolved_user,
                file_library=file_library,
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
            self._require_active(resolved_user)
            file_library = await self._file_library_for_user(session, resolved_user=resolved_user)
            selected_tags = await self._file_tags_by_ids(
                session,
                file_library_id=file_library.id,
                tag_ids=tag_ids,
            )
            matching_files = self._filter_files(
                file_library=file_library,
                query=query,
                selected_tags=selected_tags,
                tag_match_mode=tag_match_mode,
            )
            start = max(page - 1, 0) * page_size
            end = start + page_size
            page_files = matching_files[start:end]
            return FileListResponse(
                files=[self._file_summary(file_record, clerk_user_id=clerk_user_id) for file_record in page_files],
                total_count=len(matching_files),
                page=page,
                page_size=page_size,
                has_more=end < len(matching_files),
            )

    async def list_tags(self, *, clerk_user_id: str) -> TagListResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            resolved_user = await self._resolved_user(session, clerk_user_id=clerk_user_id)
            self._require_active(resolved_user)
            file_library = await self._file_library_for_user(session, resolved_user=resolved_user)
            return TagListResponse(
                tags=[self._tag_summary(tag) for tag in sorted(file_library.tags, key=lambda item: item.name.lower())]
            )

    async def get_file_detail(
        self,
        *,
        clerk_user_id: str,
        file_id: str,
    ) -> FileDetail:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            resolved_user = await self._resolved_user(session, clerk_user_id=clerk_user_id)
            self._require_active(resolved_user)
            file_record = await self._file_for_user(
                session,
                resolved_user=resolved_user,
                file_id=file_id,
            )
            return self._file_detail(file_record, clerk_user_id=clerk_user_id)

    async def download_file_with_token(
        self,
        *,
        file_id: str,
        token: str,
    ) -> tuple[FileDetail, bytes]:
        claims = self._session_tokens.verify_file_download(token)
        if claims is None or claims.file_id != file_id:
            raise ValueError("Invalid download token.")

        await self._database.ensure_ready()
        async with self._database.session() as session:
            resolved_user = await self._resolved_user_from_download_claims(
                session,
                claims=claims,
            )
            file_record = await self._file_for_user(
                session,
                resolved_user=resolved_user,
                file_id=file_id,
            )
            if file_record.openai_original_file_id is None:
                raise FileNotFoundError("The requested file has no stored original file.")
            detail = self._file_detail(
                file_record,
                clerk_user_id=resolved_user.summary.clerk_user_id,
            )
            payload = await self._openai_gateway.read_file_bytes(
                file_id=file_record.openai_original_file_id,
            )
            return detail, payload

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
            self._require_active(resolved_user)
            file_library = await self._file_library_for_user(session, resolved_user=resolved_user)
            if file_library.openai_vector_store_id is None:
                return []

            selected_tags = await self._file_tags_by_ids(
                session,
                file_library_id=file_library.id,
                tag_ids=tag_ids,
            )
            matching_files = self._filter_files(
                file_library=file_library,
                query=None,
                selected_tags=selected_tags,
                tag_match_mode=tag_match_mode,
            )
            all_file_ids = {file_record.id for file_record in file_library.files}
            scoped_file_ids = [file_record.id for file_record in matching_files]
            tag_slugs = [tag.slug for tag in selected_tags]
            filters = build_filter_groups(
                file_ids=[] if set(scoped_file_ids) == all_file_ids else scoped_file_ids,
                media_types=[],
                tag_slugs=tag_slugs,
                tag_match_mode=tag_match_mode,
            )
            return await self._openai_gateway.search_vector_store(
                vector_store_id=file_library.openai_vector_store_id,
                query=normalized_query,
                max_results=max_results,
                rewrite_query=True,
                filters=filters,
            )

    async def search_file_branches(
        self,
        *,
        clerk_user_id: str,
        query: str,
        tag_ids: list[str],
        tag_match_mode: TagMatchMode,
        descend: int,
        max_width: int,
    ) -> SearchBranchResponse:
        normalized_query = query.strip()
        if not normalized_query:
            return SearchBranchResponse(
                query="",
                descend=descend,
                max_width=max_width,
                tag_ids=tag_ids,
                tag_match_mode=tag_match_mode,
                levels=[],
            )

        levels: list[SearchBranchLevel] = []
        current_hits = await self.search_files(
            clerk_user_id=clerk_user_id,
            query=normalized_query,
            tag_ids=tag_ids,
            tag_match_mode=tag_match_mode,
            max_results=max_width,
        )
        if current_hits:
            levels.append(SearchBranchLevel(depth=0, hits=current_hits))

        seen_file_ids = {hit.file_id for hit in current_hits if hit.file_id}
        for depth in range(1, descend + 1):
            next_hits: list[SearchHit] = []
            for seed_hit in current_hits:
                branch_query = self._branch_query(root_query=normalized_query, search_hit=seed_hit)
                branch_hits = await self.search_files(
                    clerk_user_id=clerk_user_id,
                    query=branch_query,
                    tag_ids=tag_ids,
                    tag_match_mode=tag_match_mode,
                    max_results=max_width,
                )
                for candidate in branch_hits:
                    if not candidate.file_id or candidate.file_id in seen_file_ids:
                        continue
                    seen_file_ids.add(candidate.file_id)
                    next_hits.append(candidate)
                    if len(next_hits) >= max_width:
                        break
                if len(next_hits) >= max_width:
                    break
            if not next_hits:
                break
            levels.append(SearchBranchLevel(depth=depth, hits=next_hits))
            current_hits = next_hits

        logger.info(
            "file_library_branch_search clerk_user_id=%s query=%s descend=%s max_width=%s levels=%s",
            clerk_user_id,
            normalized_query,
            descend,
            max_width,
            len(levels),
        )
        return SearchBranchResponse(
            query=normalized_query,
            descend=descend,
            max_width=max_width,
            tag_ids=tag_ids,
            tag_match_mode=tag_match_mode,
            levels=levels,
        )

    async def resolve_tag_ids(
        self,
        *,
        clerk_user_id: str,
        tag_tokens: list[str],
    ) -> list[str]:
        normalized_tokens = [token.strip() for token in tag_tokens if token.strip()]
        if not normalized_tokens:
            return []

        await self._database.ensure_ready()
        async with self._database.session() as session:
            resolved_user = await self._resolved_user(session, clerk_user_id=clerk_user_id)
            self._require_active(resolved_user)
            file_library = await self._file_library_for_user(session, resolved_user=resolved_user)

            tag_lookup: dict[str, str] = {}
            for tag in file_library.tags:
                tag_lookup[tag.id.lower()] = tag.id
                tag_lookup[tag.slug.lower()] = tag.id
                tag_lookup[tag.name.lower()] = tag.id

            resolved_tag_ids: list[str] = []
            seen_tag_ids: set[str] = set()
            missing_tokens: list[str] = []
            for token in normalized_tokens:
                resolved_tag_id = tag_lookup.get(token.lower())
                if resolved_tag_id is None:
                    missing_tokens.append(token)
                    continue
                if resolved_tag_id in seen_tag_ids:
                    continue
                seen_tag_ids.add(resolved_tag_id)
                resolved_tag_ids.append(resolved_tag_id)

            if missing_tokens:
                raise ValueError(f"Unknown tags: {', '.join(missing_tokens)}")
            return resolved_tag_ids

    async def delete_file(
        self,
        *,
        clerk_user_id: str,
        file_id: str,
    ) -> DeleteFileResult:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            resolved_user = await self._resolved_user(session, clerk_user_id=clerk_user_id)
            self._require_active(resolved_user)
            file_library = await self._file_library_for_user(session, resolved_user=resolved_user)
            file_record = await self._file_for_user(
                session,
                resolved_user=resolved_user,
                file_id=file_id,
            )
            openai_file_ids = {
                candidate
                for candidate in [
                    file_record.openai_original_file_id,
                    *[
                        artifact.openai_file_id
                        for artifact in file_record.derived_artifacts
                        if artifact.openai_file_id is not None
                    ],
                ]
                if candidate is not None
            }
            for candidate in sorted(openai_file_ids):
                await self._openai_gateway.delete_file(file_id=candidate)
            file_library.updated_at = _utcnow()
            await session.delete(file_record)
            await session.commit()
            logger.info(
                "file_library_file_deleted file_id=%s clerk_user_id=%s",
                file_id,
                clerk_user_id,
            )
            return DeleteFileResult(deleted_file_id=file_id)

    async def _ingest_file(
        self,
        session,
        *,
        resolved_user: ResolvedUser,
        file_library: FileLibrary,
        local_path: Path,
        filename: str,
        declared_media_type: str | None,
        tag_ids: list[str],
    ) -> UploadFinalizeResult:
        await self._ensure_vector_store(session, file_library, resolved_user)

        tag_records = await self._file_tags_by_ids(
            session,
            file_library_id=file_library.id,
            tag_ids=tag_ids,
        )
        media_type = guess_media_type(local_path, declared_media_type)
        source_kind = classify_source_kind(local_path=local_path, media_type=media_type)
        display_title = await self._unique_file_title(
            session,
            file_library_id=file_library.id,
            base_title=Path(filename).stem or filename,
        )
        now = _utcnow()
        file_record = LibraryFile(
            file_library_id=file_library.id,
            uploaded_by_user_id=resolved_user.app_user.id,
            display_title=display_title,
            original_filename=filename,
            media_type=media_type,
            source_kind=source_kind,
            status="processing",
            byte_size=local_path.stat().st_size,
            original_mime_type=media_type,
            created_at=now,
            updated_at=now,
        )
        file_library.updated_at = now
        session.add(file_record)
        await session.flush()
        file_record.tag_links = [FileTagLink(file_id=file_record.id, tag_id=tag.id) for tag in tag_records]

        try:
            original_file_id = await self._openai_gateway.upload_original_file(
                local_path=local_path,
                purpose=self._openai_gateway.choose_original_file_purpose(source_kind=source_kind),
            )
            file_record.openai_original_file_id = original_file_id

            tag_names = [tag.name for tag in tag_records]
            tag_slugs = [tag.slug for tag in tag_records]

            derived_text = extract_text_document(local_path=local_path, media_type=media_type)
            if source_kind == "image":
                image_payload = await self._openai_gateway.describe_image(openai_file_id=original_file_id)
                derived_text = render_image_description(image_payload)
                await self._store_derived_artifact(
                    session=session,
                    file_library=file_library,
                    file_record=file_record,
                    kind="image_description",
                    text_content=derived_text,
                    structured_payload=image_payload.model_dump(mode="json"),
                    tag_names=tag_names,
                    tag_slugs=tag_slugs,
                )
            elif source_kind == "audio":
                derived_text, payload = await self._openai_gateway.transcribe_audio(local_path=local_path)
                await self._store_derived_artifact(
                    session=session,
                    file_library=file_library,
                    file_record=file_record,
                    kind="audio_transcript",
                    text_content=derived_text,
                    structured_payload=payload,
                    tag_names=tag_names,
                    tag_slugs=tag_slugs,
                )
            elif source_kind == "video":
                derived_text, payload = await self._openai_gateway.transcribe_video(local_path=local_path)
                await self._store_derived_artifact(
                    session=session,
                    file_library=file_library,
                    file_record=file_record,
                    kind="video_transcript",
                    text_content=derived_text,
                    structured_payload=payload,
                    tag_names=tag_names,
                    tag_slugs=tag_slugs,
                )
            elif derived_text is not None:
                await self._store_derived_artifact(
                    session=session,
                    file_library=file_library,
                    file_record=file_record,
                    kind="document_text",
                    text_content=derived_text,
                    structured_payload=None,
                    tag_names=tag_names,
                    tag_slugs=tag_slugs,
                )
            else:
                await self._openai_gateway.attach_existing_file_to_vector_store(
                    vector_store_id=file_library.openai_vector_store_id or "",
                    file_id=original_file_id,
                    attributes=build_searchable_attributes(
                        file_library_id=file_library.id,
                        file_id=file_record.id,
                        file_title=file_record.display_title,
                        derived_artifact_id=None,
                        source_kind=source_kind,
                        media_type=media_type,
                        derived_kind="direct_file",
                        original_openai_file_id=original_file_id,
                        original_filename=file_record.original_filename,
                        tag_names=tag_names,
                        tag_slugs=tag_slugs,
                    ),
                )

            file_record.status = "ready"
            file_record.error_message = None
            file_record.updated_at = _utcnow()
            file_library.updated_at = _utcnow()
            await session.commit()
        except Exception as exc:
            file_record.status = "failed"
            file_record.error_message = str(exc)
            file_record.updated_at = _utcnow()
            file_library.updated_at = _utcnow()
            await session.commit()
            raise

        logger.info(
            "file_library_file_ingested file_id=%s clerk_user_id=%s source_kind=%s status=%s",
            file_record.id,
            resolved_user.summary.clerk_user_id,
            file_record.source_kind,
            file_record.status,
        )
        return UploadFinalizeResult(
            file=self._file_summary(
                file_record,
                clerk_user_id=resolved_user.summary.clerk_user_id,
            )
        )

    async def _store_derived_artifact(
        self,
        *,
        session,
        file_library: FileLibrary,
        file_record: LibraryFile,
        kind: str,
        text_content: str,
        structured_payload,
        tag_names: list[str],
        tag_slugs: list[str],
    ) -> None:
        derived = DerivedArtifact(
            file_id=file_record.id,
            kind=kind,
            text_content=text_content,
            structured_payload=structured_payload,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(derived)
        await session.flush()
        derived.openai_file_id = await self._openai_gateway.create_text_artifact_and_attach(
            vector_store_id=file_library.openai_vector_store_id or "",
            filename=f"{file_record.original_filename}.{kind}.md",
            text_content=text_content,
            attributes=build_searchable_attributes(
                file_library_id=file_library.id,
                file_id=file_record.id,
                file_title=file_record.display_title,
                derived_artifact_id=derived.id,
                source_kind=file_record.source_kind,
                media_type=file_record.media_type,
                derived_kind=kind,
                original_openai_file_id=file_record.openai_original_file_id,
                original_filename=file_record.original_filename,
                tag_names=tag_names,
                tag_slugs=tag_slugs,
            ),
        )
        derived.updated_at = _utcnow()

    async def _resolved_user(self, session, *, clerk_user_id: str) -> ResolvedUser:
        if clerk_user_id == "local-dev":
            app_user = await self._ensure_local_dev_user(session)
            return self._resolved_user_from_app_user(app_user)

        clerk_record = await self._clerk_auth.get_user_record(clerk_user_id)
        app_user = await self._upsert_clerk_user(session, clerk_record)
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

    async def _resolved_user_from_download_claims(
        self,
        session,
        *,
        claims: FileDownloadClaims,
    ) -> ResolvedUser:
        app_user = await self._user_by_clerk_id(session, claims.clerk_user_id)
        if app_user is None:
            raise PermissionError("Download token does not map to a known user.")
        return self._resolved_user_from_app_user(app_user)

    @staticmethod
    def _resolved_user_from_app_user(app_user: AppUser) -> ResolvedUser:
        return ResolvedUser(
            app_user=app_user,
            summary=UserSummary(
                clerk_user_id=app_user.clerk_user_id,
                display_name=app_user.display_name or app_user.clerk_user_id,
                primary_email=app_user.primary_email,
                active=app_user.active,
                role=app_user.role,
            ),
        )

    async def _upsert_clerk_user(
        self,
        session,
        clerk_record: ClerkUserRecord,
    ) -> AppUser:
        existing = await self._user_by_clerk_id(session, clerk_record.clerk_user_id)
        now = _utcnow()
        if existing is None:
            existing = AppUser(
                clerk_user_id=clerk_record.clerk_user_id,
                primary_email=clerk_record.primary_email,
                display_name=clerk_record.display_name,
                active=clerk_record.active,
                role=clerk_record.role,
                last_seen_at=now,
            )
            session.add(existing)
        else:
            existing.primary_email = clerk_record.primary_email
            existing.display_name = clerk_record.display_name
            existing.active = clerk_record.active
            existing.role = clerk_record.role
            existing.last_seen_at = now
        await session.commit()
        await session.refresh(existing)
        return existing

    async def _ensure_local_dev_user(self, session) -> AppUser:
        existing = await self._user_by_clerk_id(session, "local-dev")
        now = _utcnow()
        if existing is None:
            existing = AppUser(
                clerk_user_id="local-dev",
                primary_email="local-dev@example.com",
                display_name="Local Developer",
                active=True,
                role="admin",
                last_seen_at=now,
            )
            session.add(existing)
        else:
            existing.display_name = "Local Developer"
            existing.active = True
            existing.role = "admin"
            existing.last_seen_at = now
        await session.commit()
        await session.refresh(existing)
        return existing

    async def _user_by_clerk_id(self, session, clerk_user_id: str) -> AppUser | None:
        return await session.scalar(select(AppUser).where(AppUser.clerk_user_id == clerk_user_id))

    @staticmethod
    def _require_active(resolved_user: ResolvedUser) -> None:
        if resolved_user.summary.active:
            return
        raise PermissionError("Your account is signed in but is still pending manual activation.")

    async def _file_library_for_user(
        self,
        session,
        *,
        resolved_user: ResolvedUser,
    ) -> FileLibrary:
        file_library = await session.scalar(
            select(FileLibrary)
            .where(FileLibrary.user_id == resolved_user.app_user.id)
            .options(
                selectinload(FileLibrary.tags).selectinload(FileTag.file_links),
                selectinload(FileLibrary.files).selectinload(LibraryFile.derived_artifacts),
                selectinload(FileLibrary.files)
                .selectinload(LibraryFile.tag_links)
                .selectinload(FileTagLink.tag)
                .selectinload(FileTag.file_links),
            )
        )
        if file_library is not None:
            return file_library

        file_library = FileLibrary(
            user_id=resolved_user.app_user.id,
            title=build_file_library_title(resolved_user.summary.display_name),
            description="Personal file library",
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(file_library)
        await session.commit()
        return await self._file_library_for_user(session, resolved_user=resolved_user)

    async def _ensure_vector_store(
        self,
        session,
        file_library: FileLibrary,
        resolved_user: ResolvedUser,
    ) -> None:
        if file_library.openai_vector_store_id is not None:
            return
        vector_store_id = await self._openai_gateway.create_vector_store(
            name=file_library.title,
            description=file_library.description,
            metadata={"owner": resolved_user.summary.clerk_user_id},
        )
        file_library.openai_vector_store_id = vector_store_id
        file_library.updated_at = _utcnow()
        await session.flush()

    async def _file_for_user(
        self,
        session,
        *,
        resolved_user: ResolvedUser,
        file_id: str,
    ) -> LibraryFile:
        file_record = await session.scalar(
            select(LibraryFile)
            .join(FileLibrary, FileLibrary.id == LibraryFile.file_library_id)
            .where(
                LibraryFile.id == file_id,
                FileLibrary.user_id == resolved_user.app_user.id,
            )
            .options(
                selectinload(LibraryFile.derived_artifacts),
                selectinload(LibraryFile.tag_links).selectinload(FileTagLink.tag).selectinload(FileTag.file_links),
            )
        )
        if file_record is None:
            raise PermissionError("File not found or not owned by the current user.")
        return file_record

    async def _file_tags_by_ids(
        self,
        session,
        *,
        file_library_id: str,
        tag_ids: list[str],
    ) -> list[FileTag]:
        if not tag_ids:
            return []
        records = (
            (
                await session.execute(
                    select(FileTag).where(
                        FileTag.file_library_id == file_library_id,
                        FileTag.id.in_(tag_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(records) != len(set(tag_ids)):
            raise ValueError("One or more tag IDs are invalid for this file library.")
        return sorted(records, key=lambda tag: tag.name.lower())

    async def _unique_file_title(
        self,
        session,
        *,
        file_library_id: str,
        base_title: str,
    ) -> str:
        default_title = base_title.strip() or "Untitled file"
        candidate = default_title
        suffix = 2
        while True:
            existing = await session.scalar(
                select(LibraryFile.id).where(
                    LibraryFile.file_library_id == file_library_id,
                    func.lower(LibraryFile.display_title) == candidate.lower(),
                )
            )
            if existing is None:
                return candidate
            candidate = f"{default_title} ({suffix})"
            suffix += 1

    @staticmethod
    def _tag_summary(tag: FileTag) -> FileTagSummary:
        return FileTagSummary(
            id=tag.id,
            name=tag.name,
            slug=tag.slug,
            color=tag.color,
            file_count=len(tag.file_links),
        )

    def _file_summary(self, file_record: LibraryFile, *, clerk_user_id: str) -> FileSummary:
        return FileSummary(
            id=file_record.id,
            display_title=file_record.display_title,
            original_filename=file_record.original_filename,
            media_type=file_record.media_type,
            source_kind=file_record.source_kind,  # type: ignore[arg-type]
            status=file_record.status,  # type: ignore[arg-type]
            byte_size=file_record.byte_size,
            error_message=file_record.error_message,
            created_at=file_record.created_at,
            updated_at=file_record.updated_at,
            tags=[
                self._tag_summary(link.tag)
                for link in sorted(file_record.tag_links, key=lambda link: link.tag.name.lower())
            ],
            derived_kinds=sorted(artifact.kind for artifact in file_record.derived_artifacts),
            openai_original_file_id=file_record.openai_original_file_id,
            download_url=self._session_tokens.issue_file_download_url(
                clerk_user_id=clerk_user_id,
                file_id=file_record.id,
            )
            if file_record.openai_original_file_id
            else None,
        )

    def _file_detail(self, file_record: LibraryFile, *, clerk_user_id: str) -> FileDetail:
        summary = self._file_summary(file_record, clerk_user_id=clerk_user_id)
        return FileDetail(
            **summary.model_dump(mode="python"),
            original_mime_type=file_record.original_mime_type,
            derived_artifacts=[
                DerivedArtifactSummary(
                    id=artifact.id,
                    kind=artifact.kind,
                    openai_file_id=artifact.openai_file_id,
                    text_content=artifact.text_content,
                    structured_payload=artifact.structured_payload,
                    created_at=artifact.created_at,
                    updated_at=artifact.updated_at,
                )
                for artifact in sorted(file_record.derived_artifacts, key=lambda item: item.created_at)
            ],
        )

    def _filter_files(
        self,
        *,
        file_library: FileLibrary,
        query: str | None,
        selected_tags: list[FileTag],
        tag_match_mode: TagMatchMode,
    ) -> list[LibraryFile]:
        normalized_query = query.strip().lower() if isinstance(query, str) and query.strip() else None
        selected_tag_ids = {tag.id for tag in selected_tags}
        matching_files: list[LibraryFile] = []
        for file_record in sorted(
            file_library.files,
            key=lambda item: (item.updated_at, item.created_at),
            reverse=True,
        ):
            file_tag_ids = {link.tag_id for link in file_record.tag_links}
            if selected_tag_ids:
                if tag_match_mode == "all" and not selected_tag_ids.issubset(file_tag_ids):
                    continue
                if tag_match_mode == "any" and not selected_tag_ids.intersection(file_tag_ids):
                    continue
            if normalized_query is not None and not self._matches_query(file_record, normalized_query):
                continue
            matching_files.append(file_record)
        return matching_files

    @staticmethod
    def _matches_query(file_record: LibraryFile, query: str) -> bool:
        fields = [
            file_record.display_title,
            file_record.original_filename,
            file_record.media_type,
            file_record.source_kind,
            *[link.tag.name for link in file_record.tag_links],
            *[artifact.text_content[:2_000] for artifact in file_record.derived_artifacts],
        ]
        haystack = "\n".join(field for field in fields if field).lower()
        return query in haystack

    @staticmethod
    def _branch_query(*, root_query: str, search_hit: SearchHit) -> str:
        parts = [root_query, search_hit.file_title]
        if search_hit.tags:
            parts.append(", ".join(search_hit.tags))
        if search_hit.text:
            parts.append(search_hit.text[:1_200])
        return "\n\n".join(part.strip() for part in parts if part and part.strip())


def build_file_library_title(display_name: str) -> str:
    normalized = display_name.strip() or "User"
    if normalized.endswith("s"):
        return f"{normalized}' File Library"
    return f"{normalized}'s File Library"


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if normalized:
        return normalized[:72]
    return "tag"


def classify_source_kind(*, local_path: Path, media_type: str) -> str:
    if media_type.startswith("image/"):
        return "image"
    if media_type.startswith("audio/"):
        return "audio"
    if media_type.startswith("video/"):
        return "video"
    if media_type.startswith("text/") or local_path.suffix.lower() in TEXT_EXTENSIONS:
        return "document"
    if media_type in {
        "application/json",
        "application/xml",
        "application/x-yaml",
    }:
        return "document"
    return "document"


def extract_text_document(*, local_path: Path, media_type: str) -> str | None:
    suffix = local_path.suffix.lower()
    if not (media_type.startswith("text/") or suffix in TEXT_EXTENSIONS):
        return None
    raw_bytes = local_path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        return normalized or None
    return None


def render_image_description(payload) -> str:
    lines = [payload.summary, "", payload.detailed_description]
    if payload.visible_text:
        lines.append("")
        lines.append("Visible text:")
        lines.extend(f"- {item}" for item in payload.visible_text)
    if payload.keywords:
        lines.append("")
        lines.append(f"Keywords: {', '.join(payload.keywords)}")
    return "\n".join(line for line in lines if line is not None).strip()


def _utcnow() -> datetime:
    return datetime.now(UTC)
