from __future__ import annotations

from collections.abc import AsyncIterator
import logging
from typing import Any, cast

import httpx
from agents import Agent, Runner
from agents.mcp import MCPServerStreamableHttp
from agents.model_settings import ModelSettings
from chatkit.agents import AgentContext as ChatKitAgentContext
from chatkit.agents import ThreadItemConverter, stream_agent_response
from chatkit.server import ChatKitServer
from chatkit.types import ChatKitReq, ThreadMetadata, ThreadStreamEvent, UserMessageItem
from fastapi import HTTPException, status
from openai.types.responses.response_input_item_param import Message, ResponseInputItemParam
from pydantic import TypeAdapter

from .chat_metadata import (
    AppChatMetadata,
    ChatMetadataPatch,
    ChatRequestMetadata,
    merge_chat_metadata,
    parse_chat_metadata,
    parse_chat_request_metadata,
)
from .chat_store import FileDeskChatContext, FileDeskChatStore
from .chat_usage import accumulate_usage
from .file_library_service import FileLibraryService
from .settings import AppSettings

logger = logging.getLogger("chatkit.server")

MODEL_ALIASES = {
    "default": "gpt-5.4-mini",
    "lightweight": "gpt-5.4-nano",
    "balanced": "gpt-5.4-mini",
    "powerful": "gpt-5.4",
}
DEFAULT_MODEL = MODEL_ALIASES["balanced"]
MAX_AGENT_TURNS = 20


class FileDeskChatKitServer(ChatKitServer[FileDeskChatContext]):
    def __init__(
        self,
        *,
        settings: AppSettings,
        store: FileDeskChatStore,
        file_library: FileLibraryService,
    ) -> None:
        super().__init__(store=store)
        self._settings = settings
        self._file_library = file_library
        self._converter = ThreadItemConverter()

    async def build_request_context(
        self,
        raw_request: bytes | str,
        *,
        clerk_user_id: str,
        user_email: str | None,
        display_name: str,
        bearer_token: str,
        request_app: Any,
    ) -> FileDeskChatContext:
        parsed_request = TypeAdapter(ChatKitReq).validate_json(raw_request)
        try:
            request_metadata = parse_chat_request_metadata(parsed_request.metadata)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        return FileDeskChatContext(
            clerk_user_id=clerk_user_id,
            user_email=user_email,
            display_name=display_name,
            bearer_token=bearer_token,
            selected_file_ids=list(request_metadata.get("selected_file_ids", [])),
            thread_origin=_normalize_origin(request_metadata),
            request_app=request_app,
        )

    def respond(
        self,
        thread: ThreadMetadata,
        input_user_message: UserMessageItem | None,
        context: FileDeskChatContext,
    ) -> AsyncIterator[ThreadStreamEvent]:
        return self._respond(thread=thread, input_user_message=input_user_message, context=context)

    async def _respond(
        self,
        *,
        thread: ThreadMetadata,
        input_user_message: UserMessageItem | None,
        context: FileDeskChatContext,
    ) -> AsyncIterator[ThreadStreamEvent]:
        typed_metadata = parse_chat_metadata(thread.metadata)
        requested_model = self._resolve_requested_model(input_user_message=input_user_message)
        if thread.title is None and input_user_message is not None:
            resolved_title = _title_from_user_message(input_user_message)
            if resolved_title is not None:
                thread.title = resolved_title

        history = await self.store.load_thread_items(
            thread.id,
            after=None,
            limit=100,
            order="asc",
            context=context,
        )
        agent_input = cast(
            list[ResponseInputItemParam],
            await self._converter.to_agent_input(history.data),
        )
        selected_file_context = await self._selected_file_context_items(context=context)
        if selected_file_context:
            agent_input = selected_file_context + agent_input

        agent_context = ChatKitAgentContext[FileDeskChatContext](
            thread=thread,
            store=self.store,
            request_context=context,
        )
        mcp_server = self._build_mcp_server(context)
        async with mcp_server:
            agent = Agent[ChatKitAgentContext[FileDeskChatContext]](
                name="file_desk_agent",
                model=requested_model,
                model_settings=_model_settings_override_for_model(requested_model) or ModelSettings(),
                mcp_servers=[mcp_server],
                instructions=self._agent_instructions,
            )
            result = Runner.run_streamed(
                agent,
                agent_input,
                context=agent_context,
                max_turns=MAX_AGENT_TURNS,
            )
            async for event in stream_agent_response(agent_context, result):
                yield event

        usage = typed_metadata.get("usage")
        for response in result.raw_responses:
            usage = accumulate_usage(usage, response.usage, model=requested_model)

        patch: ChatMetadataPatch = {}
        if thread.title:
            patch["title"] = thread.title
        if usage is not None:
            patch["usage"] = usage
        if result.last_response_id:
            patch["openai_previous_response_id"] = result.last_response_id
        self._apply_metadata_patch(thread, patch=patch)

    def _apply_metadata_patch(
        self,
        thread: ThreadMetadata,
        *,
        patch: ChatMetadataPatch,
    ) -> AppChatMetadata:
        merged_metadata = merge_chat_metadata(parse_chat_metadata(thread.metadata), patch)
        thread.metadata = dict(merged_metadata)
        return merged_metadata

    def _build_mcp_server(self, context: FileDeskChatContext) -> MCPServerStreamableHttp:
        def httpx_client_factory(
            headers: dict[str, str] | None = None,
            timeout: httpx.Timeout | None = None,
            auth: httpx.Auth | None = None,
        ) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=context.request_app),
                base_url=self._settings.normalized_app_base_url,
                follow_redirects=True,
                headers=headers,
                timeout=timeout,
                auth=auth,
            )

        return MCPServerStreamableHttp(
            params={
                "url": f"{self._settings.normalized_app_base_url}/mcp",
                "headers": {
                    "Authorization": f"Bearer {context.bearer_token}",
                },
                "httpx_client_factory": httpx_client_factory,
            },
            name="file_desk_mcp",
        )

    def _resolve_requested_model(self, *, input_user_message: UserMessageItem | None) -> str:
        requested_model = None
        if input_user_message is not None:
            requested_model = input_user_message.inference_options.model
        if requested_model is None:
            return DEFAULT_MODEL
        normalized = requested_model.strip()
        if not normalized:
            return DEFAULT_MODEL
        return MODEL_ALIASES.get(normalized, normalized)

    async def _selected_file_context_items(
        self,
        *,
        context: FileDeskChatContext,
    ) -> list[ResponseInputItemParam]:
        if not context.selected_file_ids:
            return []

        file_lines: list[str] = []
        for file_id in context.selected_file_ids[:8]:
            try:
                detail = await self._file_library.get_file_detail(
                    clerk_user_id=context.clerk_user_id,
                    file_id=file_id,
                )
            except Exception:
                continue
            file_lines.append(
                f"- {detail.display_title} ({detail.id}, {detail.media_type}, tags: "
                f"{', '.join(tag.name for tag in detail.tags) or 'none'})"
            )
        if not file_lines:
            return []

        return [
            cast(
                ResponseInputItemParam,
                Message(
                    role="user",
                    type="message",
                    content=[
                        {
                            "type": "input_text",
                            "text": (
                                "The user currently has these files selected in the file explorer. "
                                "Use them as the first place to look before widening the search.\n"
                                + "\n".join(file_lines)
                            ),
                        }
                    ],
                ),
            )
        ]

    @staticmethod
    async def _agent_instructions(
        _context,
        _agent,
    ) -> str:
        return (
            "You are the file desk assistant for a personal document library. "
            "Use the MCP tools to list files, inspect file details, read extracted text, and "
            "run semantic search over the user's uploaded files. Start with the user's selected "
            "files when that context is available. Be concise, grounded in retrieved content, and "
            "say when you cannot find supporting evidence in the library."
        )


def _normalize_origin(request_metadata: ChatRequestMetadata) -> str | None:
    origin = request_metadata.get("origin")
    if isinstance(origin, str) and origin.strip():
        return origin.strip()
    return None


def _title_from_user_message(item: UserMessageItem) -> str | None:
    text_parts = [
        part.text.strip()
        for part in item.content
        if getattr(part, "type", None) == "text" and isinstance(part.text, str)
    ]
    combined = " ".join(part for part in text_parts if part).strip()
    if not combined:
        return None
    if len(combined) <= 72:
        return combined
    return combined[:69].rstrip() + "..."


def _model_settings_override_for_model(model: str | None) -> ModelSettings | None:
    if not isinstance(model, str) or not model.startswith("gpt-5.4"):
        return None
    return ModelSettings(reasoning={"effort": "low", "summary": "auto"})
