from __future__ import annotations

from typing import Literal, TypedDict, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ThreadUsageTotalsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int
    output_tokens: int
    cost_usd: float


class AppChatMetadataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    openai_conversation_id: str | None = None
    openai_previous_response_id: str | None = None
    usage: ThreadUsageTotalsModel | None = None

    @field_validator(
        "title",
        "openai_conversation_id",
        "openai_previous_response_id",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("expected a string or null")
        stripped = value.strip()
        if not stripped:
            raise ValueError("expected a non-empty string")
        return stripped


class ChatRequestMetadataModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    origin: Literal["interactive", "ui_integration_test"] | str | None = None
    selected_file_ids: list[str] = Field(default_factory=list)

    @field_validator("selected_file_ids", mode="before")
    @classmethod
    def _normalize_selected_file_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("selected_file_ids must be an array")
        selected_file_ids: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("selected_file_ids entries must be strings")
            normalized = item.strip()
            if normalized:
                selected_file_ids.append(normalized)
        return selected_file_ids


class ChatMetadataPatch(TypedDict, total=False):
    title: str | None
    openai_conversation_id: str | None
    openai_previous_response_id: str | None
    usage: ThreadUsageTotalsModel | dict[str, object] | None


class AppChatMetadata(TypedDict, total=False):
    title: str
    openai_conversation_id: str
    openai_previous_response_id: str
    usage: dict[str, object]


class ChatRequestMetadata(TypedDict, total=False):
    origin: str
    selected_file_ids: list[str]


def parse_chat_metadata(value: object) -> AppChatMetadata:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("chat metadata must be an object")
    parsed = AppChatMetadataModel.model_validate(value)
    return cast(
        AppChatMetadata,
        parsed.model_dump(mode="json", exclude_none=True),
    )


def parse_chat_request_metadata(value: object) -> ChatRequestMetadata:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("request metadata must be an object")
    parsed = ChatRequestMetadataModel.model_validate(value)
    return cast(
        ChatRequestMetadata,
        parsed.model_dump(mode="json", exclude_none=True),
    )


def merge_chat_metadata(
    current: AppChatMetadata | dict[str, object] | None,
    patch: ChatMetadataPatch | dict[str, object],
) -> AppChatMetadata:
    merged: dict[str, object] = dict(parse_chat_metadata(current))
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
            continue
        merged[key] = value
    return parse_chat_metadata(merged)
