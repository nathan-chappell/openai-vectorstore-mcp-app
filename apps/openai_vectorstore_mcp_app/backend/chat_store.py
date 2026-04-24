from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from chatkit.store import NotFoundError, Store
from chatkit.types import Attachment, Page, ThreadItem, ThreadMetadata
from pydantic import TypeAdapter
from sqlalchemy import delete, func, select

from .clerk import ClerkAuthService
from .db import DatabaseManager
from .knowledge_base_service import KnowledgeBaseService
from .models import AppChatAttachment, AppChatEntry, AppChatThread, AppUser

THREAD_ITEM_ADAPTER = TypeAdapter(ThreadItem)
ATTACHMENT_ADAPTER = TypeAdapter(Attachment)


@dataclass(slots=True)
class FileDeskChatContext:
    clerk_user_id: str
    user_email: str | None
    display_name: str
    bearer_token: str
    selected_file_ids: list[str]
    thread_origin: str | None
    request_app: Any


class FileDeskChatStore(Store[FileDeskChatContext]):
    def __init__(
        self,
        *,
        database: DatabaseManager,
        clerk_auth: ClerkAuthService,
        legacy_service: KnowledgeBaseService,
    ) -> None:
        self._database = database
        self._clerk_auth = clerk_auth
        self._legacy = legacy_service

    def generate_thread_id(self, context: FileDeskChatContext) -> str:
        return f"chat_{uuid4().hex}"

    async def load_thread(
        self,
        thread_id: str,
        context: FileDeskChatContext,
    ) -> ThreadMetadata:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            record = await session.scalar(
                select(AppChatThread).where(
                    AppChatThread.id == thread_id,
                    AppChatThread.user_id == app_user.id,
                )
            )
            if record is None:
                raise NotFoundError(f"Thread {thread_id} was not found")
            return self._to_thread_metadata(record)

    async def save_thread(
        self,
        thread: ThreadMetadata,
        context: FileDeskChatContext,
    ) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            record = await session.scalar(
                select(AppChatThread).where(
                    AppChatThread.id == thread.id,
                    AppChatThread.user_id == app_user.id,
                )
            )
            next_sequence = await self._next_thread_sequence(session)
            if record is None:
                session.add(
                    AppChatThread(
                        id=thread.id,
                        user_id=app_user.id,
                        title=thread.title,
                        metadata_json=self._metadata_dict(thread.metadata),
                        status_json=thread.status.model_dump(mode="json"),
                        allowed_image_domains_json=thread.allowed_image_domains,
                        updated_sequence=next_sequence,
                        created_at=thread.created_at,
                        updated_at=datetime.now(UTC),
                    )
                )
            else:
                record.title = thread.title
                record.metadata_json = self._metadata_dict(thread.metadata)
                record.status_json = thread.status.model_dump(mode="json")
                record.allowed_image_domains_json = thread.allowed_image_domains
                record.updated_sequence = next_sequence
                record.updated_at = datetime.now(UTC)
            await session.commit()

    async def load_thread_items(
        self,
        thread_id: str,
        after: str | None,
        limit: int,
        order: str,
        context: FileDeskChatContext,
    ) -> Page[ThreadItem]:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            await self._require_thread_record(session, thread_id=thread_id, user_id=app_user.id)
            query = (
                select(AppChatEntry)
                .join(AppChatThread, AppChatThread.id == AppChatEntry.thread_id)
                .where(
                    AppChatEntry.thread_id == thread_id,
                    AppChatThread.user_id == app_user.id,
                )
            )
            query = await self._apply_item_cursor(session, query, after=after, order=order)
            query = query.order_by(
                AppChatEntry.sequence.desc() if order == "desc" else AppChatEntry.sequence.asc()
            ).limit(limit + 1)
            result = await session.execute(query)
            records = list(result.scalars().all())
            has_more = len(records) > limit
            page_records = records[:limit]
            return Page[ThreadItem](
                data=[self._to_thread_item(record) for record in page_records],
                has_more=has_more,
                after=page_records[-1].id if has_more and page_records else None,
            )

    async def save_attachment(
        self,
        attachment: Attachment,
        context: FileDeskChatContext,
    ) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            payload = attachment.model_dump(mode="json")
            record = await session.get(AppChatAttachment, attachment.id)
            if record is None:
                session.add(
                    AppChatAttachment(
                        id=attachment.id,
                        user_id=app_user.id,
                        kind=attachment.type,
                        payload=payload,
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                )
            else:
                record.kind = attachment.type
                record.payload = payload
                record.updated_at = datetime.now(UTC)
            await session.commit()

    async def load_attachment(
        self,
        attachment_id: str,
        context: FileDeskChatContext,
    ) -> Attachment:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            record = await session.scalar(
                select(AppChatAttachment).where(
                    AppChatAttachment.id == attachment_id,
                    AppChatAttachment.user_id == app_user.id,
                )
            )
            if record is None:
                raise NotFoundError(f"Attachment {attachment_id} was not found")
            return ATTACHMENT_ADAPTER.validate_python(record.payload)

    async def delete_attachment(
        self,
        attachment_id: str,
        context: FileDeskChatContext,
    ) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            await session.execute(
                delete(AppChatAttachment).where(
                    AppChatAttachment.id == attachment_id,
                    AppChatAttachment.user_id == app_user.id,
                )
            )
            await session.commit()

    async def load_threads(
        self,
        limit: int,
        after: str | None,
        order: str,
        context: FileDeskChatContext,
    ) -> Page[ThreadMetadata]:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            query = select(AppChatThread).where(AppChatThread.user_id == app_user.id)
            query = await self._apply_thread_cursor(session, query, after=after, order=order)
            query = query.order_by(
                AppChatThread.updated_sequence.desc() if order == "desc" else AppChatThread.updated_sequence.asc()
            ).limit(limit + 1)
            result = await session.execute(query)
            records = list(result.scalars().all())
            has_more = len(records) > limit
            page_records = records[:limit]
            return Page[ThreadMetadata](
                data=[self._to_thread_metadata(record) for record in page_records],
                has_more=has_more,
                after=page_records[-1].id if has_more and page_records else None,
            )

    async def add_thread_item(
        self,
        thread_id: str,
        item: ThreadItem,
        context: FileDeskChatContext,
    ) -> None:
        await self._save_thread_item(thread_id=thread_id, item=item, context=context, create_only=True)

    async def save_item(
        self,
        thread_id: str,
        item: ThreadItem,
        context: FileDeskChatContext,
    ) -> None:
        await self._save_thread_item(thread_id=thread_id, item=item, context=context, create_only=False)

    async def load_item(
        self,
        thread_id: str,
        item_id: str,
        context: FileDeskChatContext,
    ) -> ThreadItem:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            await self._require_thread_record(session, thread_id=thread_id, user_id=app_user.id)
            record = await session.scalar(
                select(AppChatEntry).where(
                    AppChatEntry.thread_id == thread_id,
                    AppChatEntry.id == item_id,
                )
            )
            if record is None:
                raise NotFoundError(f"Thread item {item_id} was not found")
            return self._to_thread_item(record)

    async def delete_thread(
        self,
        thread_id: str,
        context: FileDeskChatContext,
    ) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            record = await self._require_thread_record(session, thread_id=thread_id, user_id=app_user.id)
            await session.delete(record)
            await session.commit()

    async def delete_thread_item(
        self,
        thread_id: str,
        item_id: str,
        context: FileDeskChatContext,
    ) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            await self._require_thread_record(session, thread_id=thread_id, user_id=app_user.id)
            await session.execute(
                delete(AppChatEntry).where(
                    AppChatEntry.thread_id == thread_id,
                    AppChatEntry.id == item_id,
                )
            )
            await self._touch_thread(session, thread_id=thread_id)
            await session.commit()

    async def _save_thread_item(
        self,
        *,
        thread_id: str,
        item: ThreadItem,
        context: FileDeskChatContext,
        create_only: bool,
    ) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            await self._require_thread_record(session, thread_id=thread_id, user_id=app_user.id)
            record = await session.scalar(
                select(AppChatEntry).where(
                    AppChatEntry.thread_id == thread_id,
                    AppChatEntry.id == item.id,
                )
            )
            if record is None:
                session.add(
                    AppChatEntry(
                        id=item.id,
                        thread_id=thread_id,
                        sequence=await self._next_item_sequence(session, thread_id=thread_id),
                        item_type=item.type,
                        payload=item.model_dump(mode="json"),
                        created_at=item.created_at,
                    )
                )
            else:
                if create_only:
                    return
                record.item_type = item.type
                record.payload = item.model_dump(mode="json")
            await self._touch_thread(session, thread_id=thread_id)
            await session.commit()

    async def _touch_thread(self, session, *, thread_id: str) -> None:
        record = await session.get(AppChatThread, thread_id)
        if record is None:
            raise NotFoundError(f"Thread {thread_id} was not found")
        record.updated_sequence = await self._next_thread_sequence(session)
        record.updated_at = datetime.now(UTC)

    async def _ensure_app_user(self, session, *, clerk_user_id: str) -> AppUser:
        existing = await self._legacy._user_by_clerk_id(session, clerk_user_id)
        if existing is not None:
            return existing
        clerk_record = await self._clerk_auth.get_user_record(clerk_user_id)
        return await self._legacy._upsert_clerk_user(session, clerk_record)

    async def _require_thread_record(self, session, *, thread_id: str, user_id: int) -> AppChatThread:
        record = await session.scalar(
            select(AppChatThread).where(
                AppChatThread.id == thread_id,
                AppChatThread.user_id == user_id,
            )
        )
        if record is None:
            raise NotFoundError(f"Thread {thread_id} was not found")
        return record

    async def _apply_item_cursor(self, session, query, *, after: str | None, order: str):
        if after is None:
            return query
        cursor_sequence = await session.scalar(select(AppChatEntry.sequence).where(AppChatEntry.id == after))
        if cursor_sequence is None:
            return query
        if order == "desc":
            return query.where(AppChatEntry.sequence < cursor_sequence)
        return query.where(AppChatEntry.sequence > cursor_sequence)

    async def _apply_thread_cursor(self, session, query, *, after: str | None, order: str):
        if after is None:
            return query
        cursor_sequence = await session.scalar(select(AppChatThread.updated_sequence).where(AppChatThread.id == after))
        if cursor_sequence is None:
            return query
        if order == "desc":
            return query.where(AppChatThread.updated_sequence < cursor_sequence)
        return query.where(AppChatThread.updated_sequence > cursor_sequence)

    async def _next_item_sequence(self, session, *, thread_id: str) -> int:
        value = await session.scalar(
            select(func.coalesce(func.max(AppChatEntry.sequence), 0) + 1).where(AppChatEntry.thread_id == thread_id)
        )
        return int(value or 1)

    async def _next_thread_sequence(self, session) -> int:
        value = await session.scalar(select(func.coalesce(func.max(AppChatThread.updated_sequence), 0) + 1))
        return int(value or 1)

    @staticmethod
    def _metadata_dict(value: dict[str, object] | None) -> dict[str, object]:
        if isinstance(value, dict):
            return value
        return {}

    @staticmethod
    def _to_thread_metadata(record: AppChatThread) -> ThreadMetadata:
        return ThreadMetadata.model_validate(
            {
                "id": record.id,
                "title": record.title,
                "created_at": record.created_at,
                "status": record.status_json or {"type": "active"},
                "allowed_image_domains": record.allowed_image_domains_json,
                "metadata": record.metadata_json or {},
            }
        )

    @staticmethod
    def _to_thread_item(record: AppChatEntry) -> ThreadItem:
        return THREAD_ITEM_ADAPTER.validate_python(record.payload)
