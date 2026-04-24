from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypeAlias

from openai.types.vector_store_search_response import VectorStoreSearchResponse
from pydantic import BaseModel, Field

StructuredPayload: TypeAlias = dict[str, Any] | list[Any] | None
OpenAIAttributeValue: TypeAlias = str | float | bool
OpenAIAttributes: TypeAlias = dict[str, OpenAIAttributeValue]
FileStatus: TypeAlias = Literal["processing", "ready", "failed"]
SourceKind: TypeAlias = Literal["document", "audio", "image", "video", "other"]
TagMatchMode: TypeAlias = Literal["all", "any"]


def _read_text_from_search_result(search_result: VectorStoreSearchResponse) -> str:
    return "\n".join(content.text for content in search_result.content if content.type == "text").strip()


def _extract_tags(attributes: OpenAIAttributes | None) -> list[str]:
    if attributes is None:
        return []
    raw_tag_names = attributes.get("tag_names")
    if not isinstance(raw_tag_names, str) or not raw_tag_names:
        return []
    return [part for part in raw_tag_names.split(",") if part]


def _string_attribute(attributes: OpenAIAttributes | None, key: str) -> str | None:
    value = (attributes or {}).get(key)
    return value if isinstance(value, str) else None


class UserSummary(BaseModel):
    clerk_user_id: str
    display_name: str
    primary_email: str | None = None
    active: bool
    role: str | None = None


class FileTagSummary(BaseModel):
    id: str
    name: str
    slug: str
    color: str | None = None
    file_count: int = 0


class DerivedArtifactSummary(BaseModel):
    id: str
    kind: str
    openai_file_id: str | None = None
    text_content: str
    structured_payload: StructuredPayload = None
    created_at: datetime
    updated_at: datetime


class FileSummary(BaseModel):
    id: str
    display_title: str
    original_filename: str
    media_type: str
    source_kind: SourceKind
    status: FileStatus
    byte_size: int
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    tags: list[FileTagSummary] = Field(default_factory=list)
    derived_kinds: list[str] = Field(default_factory=list)
    openai_original_file_id: str | None = None
    download_url: str | None = None


class FileDetail(FileSummary):
    original_mime_type: str | None = None
    derived_artifacts: list[DerivedArtifactSummary] = Field(default_factory=list)


class FileListResponse(BaseModel):
    files: list[FileSummary] = Field(default_factory=list)
    total_count: int
    page: int
    page_size: int
    has_more: bool


class TagListResponse(BaseModel):
    tags: list[FileTagSummary] = Field(default_factory=list)


class DeleteFileResult(BaseModel):
    deleted_file_id: str


class SearchHit(BaseModel):
    file_id: str
    file_title: str
    original_filename: str
    derived_artifact_id: str | None = None
    openai_file_id: str
    original_openai_file_id: str | None = None
    media_type: str
    source_kind: str
    score: float
    text: str
    tags: list[str] = Field(default_factory=list)
    attributes: OpenAIAttributes | None = None

    @classmethod
    def from_openai(cls, search_result: VectorStoreSearchResponse) -> SearchHit:
        attributes = search_result.attributes
        file_title = _string_attribute(attributes, "file_title") or search_result.filename
        original_filename = _string_attribute(attributes, "original_filename") or search_result.filename
        return cls(
            file_id=str((attributes or {}).get("file_id") or ""),
            file_title=file_title,
            original_filename=original_filename,
            derived_artifact_id=_string_attribute(attributes, "derived_artifact_id"),
            openai_file_id=search_result.file_id,
            original_openai_file_id=_string_attribute(attributes, "original_openai_file_id"),
            media_type=_string_attribute(attributes, "media_type") or "application/octet-stream",
            source_kind=_string_attribute(attributes, "source_kind") or "other",
            score=search_result.score,
            text=_read_text_from_search_result(search_result),
            tags=_extract_tags(attributes),
            attributes=attributes,
        )


class SearchBranchLevel(BaseModel):
    depth: int
    hits: list[SearchHit] = Field(default_factory=list)


class SearchBranchResponse(BaseModel):
    query: str
    descend: int
    max_width: int
    tag_ids: list[str] = Field(default_factory=list)
    tag_match_mode: TagMatchMode
    levels: list[SearchBranchLevel] = Field(default_factory=list)


class UploadSessionResult(BaseModel):
    upload_url: str
    upload_token: str
    expires_at: int


class UploadFinalizeResult(BaseModel):
    file: FileSummary


class ImageDescriptionPayload(BaseModel):
    summary: str
    detailed_description: str
    visible_text: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
