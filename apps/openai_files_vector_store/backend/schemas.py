from __future__ import annotations

from typing import TypeAlias

from openai.types.file_object import FileObject
from openai.types.responses import ResponseFileSearchToolCall
from openai.types.vector_store import FileCounts as OpenAIFileCounts
from openai.types.vector_store import VectorStore
from openai.types.vector_store_search_response import VectorStoreSearchResponse
from openai.types.vector_stores.vector_store_file import VectorStoreFile
from openai.types.vector_stores.vector_store_file_batch import (
    FileCounts as OpenAIBatchFileCounts,
)
from openai.types.vector_stores.vector_store_file_batch import VectorStoreFileBatch
from pydantic import BaseModel, Field

ToolAttributeValue: TypeAlias = str | int | float | bool
ToolAttributes: TypeAlias = dict[str, ToolAttributeValue]
OpenAIAttributeValue: TypeAlias = str | float | bool
OpenAIAttributes: TypeAlias = dict[str, OpenAIAttributeValue]
VectorStoreMetadata: TypeAlias = dict[str, str]


class FileSummary(BaseModel):
    id: str
    filename: str
    purpose: str
    bytes: int
    created_at: int
    status: str
    expires_at: int | None = None
    status_details: str | None = None

    @classmethod
    def from_openai(cls, file_object: FileObject) -> "FileSummary":
        return cls(
            id=file_object.id,
            filename=file_object.filename,
            purpose=file_object.purpose,
            bytes=file_object.bytes,
            created_at=file_object.created_at,
            status=file_object.status,
            expires_at=file_object.expires_at,
            status_details=file_object.status_details,
        )


class FileCountsSummary(BaseModel):
    completed: int
    failed: int
    in_progress: int
    cancelled: int
    total: int

    @classmethod
    def from_openai(
        cls,
        file_counts: OpenAIFileCounts | OpenAIBatchFileCounts,
    ) -> "FileCountsSummary":
        return cls(
            completed=file_counts.completed,
            failed=file_counts.failed,
            in_progress=file_counts.in_progress,
            cancelled=file_counts.cancelled,
            total=file_counts.total,
        )


class VectorStoreSummary(BaseModel):
    id: str
    name: str
    status: str
    created_at: int
    last_active_at: int | None = None
    usage_bytes: int
    expires_at: int | None = None
    metadata: VectorStoreMetadata | None = None
    file_counts: FileCountsSummary

    @classmethod
    def from_openai(cls, vector_store: VectorStore) -> "VectorStoreSummary":
        return cls(
            id=vector_store.id,
            name=vector_store.name,
            status=vector_store.status,
            created_at=vector_store.created_at,
            last_active_at=vector_store.last_active_at,
            usage_bytes=vector_store.usage_bytes,
            expires_at=vector_store.expires_at,
            metadata=vector_store.metadata,
            file_counts=FileCountsSummary.from_openai(vector_store.file_counts),
        )


class VectorStoreFileSummary(BaseModel):
    id: str
    created_at: int
    status: str
    usage_bytes: int
    vector_store_id: str
    attributes: OpenAIAttributes | None = None
    last_error: str | None = None

    @classmethod
    def from_openai(
        cls, vector_store_file: VectorStoreFile
    ) -> "VectorStoreFileSummary":
        return cls(
            id=vector_store_file.id,
            created_at=vector_store_file.created_at,
            status=vector_store_file.status,
            usage_bytes=vector_store_file.usage_bytes,
            vector_store_id=vector_store_file.vector_store_id,
            attributes=vector_store_file.attributes,
            last_error=vector_store_file.last_error.message
            if vector_store_file.last_error
            else None,
        )


class VectorStoreBatchSummary(BaseModel):
    id: str
    created_at: int
    status: str
    vector_store_id: str
    file_counts: FileCountsSummary

    @classmethod
    def from_openai(cls, batch: VectorStoreFileBatch) -> "VectorStoreBatchSummary":
        return cls(
            id=batch.id,
            created_at=batch.created_at,
            status=batch.status,
            vector_store_id=batch.vector_store_id,
            file_counts=FileCountsSummary.from_openai(batch.file_counts),
        )


class SearchHit(BaseModel):
    file_id: str
    filename: str
    score: float
    text: str
    attributes: OpenAIAttributes | None = None

    @classmethod
    def from_openai(cls, search_result: VectorStoreSearchResponse) -> "SearchHit":
        text = "\n".join(
            content.text for content in search_result.content if content.type == "text"
        )
        return cls(
            file_id=search_result.file_id,
            filename=search_result.filename,
            score=search_result.score,
            text=text,
            attributes=search_result.attributes,
        )


class FileSearchCallSummary(BaseModel):
    id: str
    status: str
    queries: list[str]
    results: list[SearchHit] = Field(default_factory=list)

    @classmethod
    def from_openai(
        cls, tool_call: ResponseFileSearchToolCall
    ) -> "FileSearchCallSummary":
        return cls(
            id=tool_call.id,
            status=tool_call.status,
            queries=list(tool_call.queries),
            results=[
                SearchHit(
                    file_id=result.file_id or "",
                    filename=result.filename or "",
                    score=result.score or 0.0,
                    text=result.text or "",
                    attributes=result.attributes,
                )
                for result in (tool_call.results or [])
            ],
        )


class FileListResult(BaseModel):
    files: list[FileSummary]
    total_returned: int
    purpose_filter: str | None = None


class FilePreviewResult(BaseModel):
    vector_store_id: str
    file_id: str
    filename: str
    bytes: int
    purpose: str
    status: str
    preview_text: str | None = None
    preview_truncated: bool = False
    preview_message: str | None = None


class VectorStoreListResult(BaseModel):
    vector_stores: list[VectorStoreSummary]
    total_returned: int


class UploadFileResult(BaseModel):
    uploaded_file: FileSummary
    vector_store_id: str | None = None
    attached_file: VectorStoreFileSummary | None = None


class AttachFilesResult(BaseModel):
    vector_store_id: str
    file_ids: list[str]
    local_paths: list[str]
    attached_files: list[VectorStoreFileSummary]
    batch: VectorStoreBatchSummary | None = None


class VectorStoreStatusResult(BaseModel):
    vector_store: VectorStoreSummary
    files: list[VectorStoreFileSummary]
    batch: VectorStoreBatchSummary | None = None
    batch_files: list[VectorStoreFileSummary] = Field(default_factory=list)


class SearchVectorStoreResult(BaseModel):
    vector_store_id: str
    query: str
    hits: list[SearchHit]
    total_hits: int


class AskVectorStoreResult(BaseModel):
    vector_store_id: str
    question: str
    answer: str
    model: str
    search_calls: list[FileSearchCallSummary]


class SearchPanelState(BaseModel):
    query: str = ""
    max_num_results: int = 5
    rewrite_query: bool = False


class AskPanelState(BaseModel):
    question: str = ""
    max_num_results: int = 5


class OpenVectorStoreConsoleResult(BaseModel):
    vector_store_list: VectorStoreListResult
    selected_vector_store_id: str | None = None
    selected_vector_store_status: VectorStoreStatusResult | None = None
    search_panel: SearchPanelState = Field(default_factory=SearchPanelState)
    ask_panel: AskPanelState = Field(default_factory=AskPanelState)
