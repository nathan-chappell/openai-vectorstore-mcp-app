from __future__ import annotations

import logging
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter
from typing import Any

from openai import OpenAI
from openai.types.file_purpose import FilePurpose

from .schemas import (
    AttachFilesResult,
    FileListResult,
    FilePreviewResult,
    FileSummary,
    OpenAIAttributes,
    SearchHit,
    SearchVectorStoreResult,
    ToolAttributes,
    UploadFileResult,
    VectorStoreBatchSummary,
    VectorStoreFileSummary,
    VectorStoreListResult,
    VectorStoreMetadata,
    VectorStoreStatusResult,
    VectorStoreSummary,
)
from .settings import AppSettings


class OpenAIFilesVectorStoreGateway:
    """Live gateway for OpenAI file and vector-store operations."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._client = OpenAI(api_key=settings.openai_api_key.get_secret_value())
        self._logger = logging.getLogger(__name__)

    def upload_file(
        self,
        *,
        local_path: str,
        vector_store_id: str | None,
        purpose: FilePurpose,
        attributes: ToolAttributes | None,
    ) -> UploadFileResult:
        started_at = perf_counter()
        resolved_path = self._resolve_local_path(local_path)

        with resolved_path.open("rb") as file_handle:
            uploaded_file = self._client.files.create(file=file_handle, purpose=purpose)

        attached_file: VectorStoreFileSummary | None = None
        normalized_attributes = self._normalize_attributes(attributes)
        if vector_store_id is not None:
            create_kwargs: dict[str, Any] = {
                "file_id": uploaded_file.id,
                "vector_store_id": vector_store_id,
                "poll_interval_ms": self._settings.openai_poll_interval_ms,
            }
            if normalized_attributes:
                create_kwargs["attributes"] = normalized_attributes

            vector_store_file = self._client.vector_stores.files.create_and_poll(
                **create_kwargs
            )
            attached_file = VectorStoreFileSummary.from_openai(vector_store_file)

        duration_ms = (perf_counter() - started_at) * 1000
        self._logger.info(
            "openai_file_upload file_id=%s vector_store_id=%s status=%s duration_ms=%.1f",
            uploaded_file.id,
            vector_store_id,
            attached_file.status if attached_file else uploaded_file.status,
            duration_ms,
        )

        return UploadFileResult(
            uploaded_file=FileSummary.from_openai(uploaded_file),
            vector_store_id=vector_store_id,
            attached_file=attached_file,
        )

    def list_files(
        self,
        *,
        limit: int,
        purpose: str | None,
    ) -> FileListResult:
        started_at = perf_counter()
        page = self._client.files.list(limit=limit)
        files = [FileSummary.from_openai(file_object) for file_object in page.data]
        if purpose is not None:
            files = [
                file_object for file_object in files if file_object.purpose == purpose
            ]

        duration_ms = (perf_counter() - started_at) * 1000
        self._logger.info(
            "openai_file_list returned=%s purpose_filter=%s duration_ms=%.1f",
            len(files),
            purpose,
            duration_ms,
        )

        return FileListResult(
            files=files,
            total_returned=len(files),
            purpose_filter=purpose,
        )

    def preview_file(
        self,
        *,
        file_id: str,
        vector_store_id: str,
        max_chars: int,
    ) -> FilePreviewResult:
        started_at = perf_counter()
        file_object = self._client.files.retrieve(file_id)
        parsed_contents_page = self._client.vector_stores.files.content(
            file_id,
            vector_store_id=vector_store_id,
        )
        parsed_text = "\n\n".join(
            content_item.text
            for content_item in parsed_contents_page
            if content_item.type == "text" and content_item.text
        )
        preview_text = parsed_text[:max_chars] if parsed_text else None
        preview_truncated = len(parsed_text) > max_chars

        preview_message: str | None
        if preview_text is None:
            preview_message = (
                "No parsed text preview is available yet for this attached file."
            )
        else:
            preview_message = (
                f"Showing the first {max_chars:,} characters of the parsed file content."
                if preview_truncated
                else None
            )

        duration_ms = (perf_counter() - started_at) * 1000
        self._logger.info(
            "openai_file_preview vector_store_id=%s file_id=%s filename=%s previewable=%s truncated=%s duration_ms=%.1f",
            vector_store_id,
            file_id,
            file_object.filename,
            preview_text is not None,
            preview_truncated,
            duration_ms,
        )

        return FilePreviewResult(
            vector_store_id=vector_store_id,
            file_id=file_object.id,
            filename=file_object.filename,
            bytes=file_object.bytes,
            purpose=file_object.purpose,
            status=file_object.status,
            preview_text=preview_text,
            preview_truncated=preview_truncated,
            preview_message=preview_message,
        )

    def create_vector_store(
        self,
        *,
        name: str | None,
        description: str | None,
        metadata: VectorStoreMetadata | None,
    ) -> VectorStoreSummary:
        started_at = perf_counter()
        create_kwargs: dict[str, Any] = {}
        if name is not None:
            create_kwargs["name"] = name
        if description is not None:
            create_kwargs["description"] = description
        if metadata:
            create_kwargs["metadata"] = metadata

        vector_store = self._client.vector_stores.create(**create_kwargs)

        duration_ms = (perf_counter() - started_at) * 1000
        self._logger.info(
            "openai_vector_store_create vector_store_id=%s status=%s duration_ms=%.1f",
            vector_store.id,
            vector_store.status,
            duration_ms,
        )

        return VectorStoreSummary.from_openai(vector_store)

    def list_vector_stores(self, *, limit: int) -> VectorStoreListResult:
        started_at = perf_counter()
        page = self._client.vector_stores.list(limit=limit)
        vector_stores = [
            VectorStoreSummary.from_openai(vector_store) for vector_store in page.data
        ]

        duration_ms = (perf_counter() - started_at) * 1000
        self._logger.info(
            "openai_vector_store_list returned=%s duration_ms=%.1f",
            len(vector_stores),
            duration_ms,
        )

        return VectorStoreListResult(
            vector_stores=vector_stores,
            total_returned=len(vector_stores),
        )

    def attach_files_to_vector_store(
        self,
        *,
        vector_store_id: str,
        file_ids: list[str] | None,
        local_paths: list[str] | None,
        attributes: ToolAttributes | None,
    ) -> AttachFilesResult:
        started_at = perf_counter()
        existing_file_ids = list(dict.fromkeys(file_ids or []))
        resolved_local_paths = [
            str(self._resolve_local_path(local_path))
            for local_path in (local_paths or [])
        ]
        normalized_attributes = self._normalize_attributes(attributes)
        total_files = len(existing_file_ids) + len(resolved_local_paths)

        if total_files == 0:
            raise ValueError("Provide at least one file_id or local_path.")

        batch_summary: VectorStoreBatchSummary | None = None
        attached_files: list[VectorStoreFileSummary]

        if total_files == 1 and existing_file_ids:
            create_kwargs: dict[str, Any] = {
                "file_id": existing_file_ids[0],
                "vector_store_id": vector_store_id,
                "poll_interval_ms": self._settings.openai_poll_interval_ms,
            }
            if normalized_attributes:
                create_kwargs["attributes"] = normalized_attributes

            vector_store_file = self._client.vector_stores.files.create_and_poll(
                **create_kwargs
            )
            attached_files = [VectorStoreFileSummary.from_openai(vector_store_file)]
        elif total_files == 1 and resolved_local_paths:
            with Path(resolved_local_paths[0]).open("rb") as file_handle:
                upload_kwargs: dict[str, Any] = {
                    "vector_store_id": vector_store_id,
                    "file": file_handle,
                    "poll_interval_ms": self._settings.openai_poll_interval_ms,
                }
                if normalized_attributes:
                    upload_kwargs["attributes"] = normalized_attributes

                vector_store_file = self._client.vector_stores.files.upload_and_poll(
                    **upload_kwargs
                )
            attached_files = [VectorStoreFileSummary.from_openai(vector_store_file)]
        else:
            with ExitStack() as exit_stack:
                file_handles = [
                    exit_stack.enter_context(Path(local_path).open("rb"))
                    for local_path in resolved_local_paths
                ]
                batch_kwargs: dict[str, Any] = {
                    "vector_store_id": vector_store_id,
                    "files": file_handles,
                    "file_ids": existing_file_ids,
                    "max_concurrency": min(total_files, 5),
                    "poll_interval_ms": self._settings.openai_poll_interval_ms,
                }
                if normalized_attributes:
                    batch_kwargs["attributes"] = normalized_attributes

                batch = self._client.vector_stores.file_batches.upload_and_poll(
                    **batch_kwargs
                )

            batch_summary = VectorStoreBatchSummary.from_openai(batch)
            batch_file_page = self._client.vector_stores.file_batches.list_files(
                batch.id,
                vector_store_id=vector_store_id,
                limit=min(max(total_files, 20), 100),
            )
            attached_files = [
                VectorStoreFileSummary.from_openai(vector_store_file)
                for vector_store_file in batch_file_page.data
            ]

        duration_ms = (perf_counter() - started_at) * 1000
        self._logger.info(
            "openai_vector_store_attach vector_store_id=%s existing_files=%s local_files=%s batch_id=%s duration_ms=%.1f",
            vector_store_id,
            len(existing_file_ids),
            len(resolved_local_paths),
            batch_summary.id if batch_summary else None,
            duration_ms,
        )

        return AttachFilesResult(
            vector_store_id=vector_store_id,
            file_ids=existing_file_ids,
            local_paths=resolved_local_paths,
            attached_files=attached_files,
            batch=batch_summary,
        )

    def get_vector_store_status(
        self,
        *,
        vector_store_id: str,
        file_limit: int,
        batch_id: str | None,
    ) -> VectorStoreStatusResult:
        started_at = perf_counter()
        vector_store = self._client.vector_stores.retrieve(vector_store_id)
        vector_store_file_page = self._client.vector_stores.files.list(
            vector_store_id,
            limit=file_limit,
        )
        files = [
            VectorStoreFileSummary.from_openai(vector_store_file)
            for vector_store_file in vector_store_file_page.data
        ]

        batch_summary: VectorStoreBatchSummary | None = None
        batch_files: list[VectorStoreFileSummary] = []
        if batch_id is not None:
            batch = self._client.vector_stores.file_batches.retrieve(
                batch_id,
                vector_store_id=vector_store_id,
            )
            batch_summary = VectorStoreBatchSummary.from_openai(batch)
            batch_file_page = self._client.vector_stores.file_batches.list_files(
                batch_id,
                vector_store_id=vector_store_id,
                limit=file_limit,
            )
            batch_files = [
                VectorStoreFileSummary.from_openai(vector_store_file)
                for vector_store_file in batch_file_page.data
            ]

        duration_ms = (perf_counter() - started_at) * 1000
        self._logger.info(
            "openai_vector_store_status vector_store_id=%s batch_id=%s file_count=%s duration_ms=%.1f",
            vector_store_id,
            batch_id,
            len(files),
            duration_ms,
        )

        return VectorStoreStatusResult(
            vector_store=VectorStoreSummary.from_openai(vector_store),
            files=files,
            batch=batch_summary,
            batch_files=batch_files,
        )

    def search_vector_store(
        self,
        *,
        vector_store_id: str,
        query: str,
        max_num_results: int,
        rewrite_query: bool,
    ) -> SearchVectorStoreResult:
        started_at = perf_counter()
        page = self._client.vector_stores.search(
            vector_store_id,
            query=query,
            max_num_results=max_num_results,
            rewrite_query=rewrite_query,
        )
        hits = [SearchHit.from_openai(search_result) for search_result in page.data]

        duration_ms = (perf_counter() - started_at) * 1000
        self._logger.info(
            "openai_vector_store_search vector_store_id=%s hits=%s duration_ms=%.1f",
            vector_store_id,
            len(hits),
            duration_ms,
        )

        return SearchVectorStoreResult(
            vector_store_id=vector_store_id,
            query=query,
            hits=hits,
            total_hits=len(hits),
        )

    def _resolve_local_path(self, local_path: str) -> Path:
        resolved_path = Path(local_path).expanduser().resolve()
        if not resolved_path.is_file():
            raise FileNotFoundError(f"Local file not found: {resolved_path}")
        return resolved_path

    def _normalize_attributes(
        self,
        attributes: ToolAttributes | None,
    ) -> OpenAIAttributes | None:
        if not attributes:
            return None

        normalized_attributes: OpenAIAttributes = {}
        for key, value in attributes.items():
            if isinstance(value, bool):
                normalized_attributes[key] = value
            elif isinstance(value, int):
                normalized_attributes[key] = float(value)
            else:
                normalized_attributes[key] = value
        return normalized_attributes
