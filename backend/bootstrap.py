from __future__ import annotations

from dataclasses import dataclass

from .chat_store import FileDeskChatStore
from .chatkit_server import FileDeskChatKitServer
from .clerk import ClerkAuthService
from .db import DatabaseManager
from .file_library_gateway import OpenAIFileLibraryGateway
from .file_library_service import FileLibraryService
from .logging import configure_logging
from .session_tokens import FileLibrarySessionService
from .settings import AppSettings


@dataclass(slots=True)
class AppServices:
    settings: AppSettings
    database: DatabaseManager
    clerk_auth: ClerkAuthService
    session_tokens: FileLibrarySessionService
    openai_gateway: OpenAIFileLibraryGateway
    file_library: FileLibraryService
    chat_store: FileDeskChatStore
    chatkit_server: FileDeskChatKitServer
    _closed: bool = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.openai_gateway.close()
        await self.clerk_auth.close()
        await self.database.close()


def create_services(settings: AppSettings) -> AppServices:
    configure_logging(settings.log_level)
    database = DatabaseManager(settings)
    clerk_auth = ClerkAuthService(settings)
    session_tokens = FileLibrarySessionService(settings)
    openai_gateway = OpenAIFileLibraryGateway(settings)
    file_library = FileLibraryService(
        database=database,
        clerk_auth=clerk_auth,
        session_tokens=session_tokens,
        openai_gateway=openai_gateway,
    )
    chat_store = FileDeskChatStore(
        database=database,
        file_library=file_library,
    )
    chatkit_server = FileDeskChatKitServer(
        settings=settings,
        store=chat_store,
        file_library=file_library,
    )
    return AppServices(
        settings=settings,
        database=database,
        clerk_auth=clerk_auth,
        session_tokens=session_tokens,
        openai_gateway=openai_gateway,
        file_library=file_library,
        chat_store=chat_store,
        chatkit_server=chatkit_server,
    )
