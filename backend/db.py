from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base
from .settings import AppSettings

_INITIALIZED_DATABASES: set[str] = set()
_INITIALIZATION_LOCKS: dict[str, asyncio.Lock] = {}


def ensure_database_directory(database_url: str) -> None:
    """Create the parent directory for local SQLite databases."""

    parsed_url = make_url(database_url)
    if parsed_url.get_backend_name() != "sqlite":
        return
    if parsed_url.database in {None, "", ":memory:"}:
        return

    database_path = Path(parsed_url.database)
    if not database_path.is_absolute():
        database_path = (Path.cwd() / database_path).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)


class AsyncSessionAdapter:
    """Expose a small async-shaped wrapper over a synchronous SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSessionAdapter:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if exc_type is not None:
            self._session.rollback()
        self._session.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    async def scalar(self, statement: Any) -> Any:
        return self._session.scalar(statement)

    async def execute(self, statement: Any) -> Any:
        return self._session.execute(statement)

    async def commit(self) -> None:
        self._session.commit()

    async def flush(self) -> None:
        self._session.flush()

    async def refresh(self, instance: object) -> None:
        self._session.refresh(instance)

    async def delete(self, instance: object) -> None:
        self._session.delete(instance)

    async def close(self) -> None:
        self._session.close()


class DatabaseManager:
    """Database access with one-time schema bootstrapping.

    SQLite defaults to a synchronous engine wrapped in an async-shaped adapter because
    this Python 3.14 environment does not reliably deliver thread callbacks needed by
    aiosqlite. Other backends continue to use SQLAlchemy's native async engine.
    """

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        parsed_url = make_url(settings.normalized_database_url)
        self._use_sync_sqlite = parsed_url.get_backend_name() == "sqlite"

        self._async_engine: AsyncEngine | None = None
        self._async_session_factory: async_sessionmaker[AsyncSession] | None = None
        self._sync_engine: Engine | None = None
        self._sync_session_factory: sessionmaker[Session] | None = None

        if self._use_sync_sqlite:
            self._sync_engine = create_engine(
                settings.sync_database_url,
                future=True,
                pool_pre_ping=True,
            )
            self._sync_session_factory = sessionmaker(
                self._sync_engine,
                class_=Session,
                expire_on_commit=False,
            )
        else:
            self._async_engine = create_async_engine(
                settings.normalized_database_url,
                future=True,
                pool_pre_ping=True,
            )
            self._async_session_factory = async_sessionmaker(
                self._async_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )

    async def ensure_ready(self) -> None:
        database_url = self._settings.normalized_database_url
        if database_url in _INITIALIZED_DATABASES:
            return

        lock = _INITIALIZATION_LOCKS.setdefault(database_url, asyncio.Lock())
        async with lock:
            if database_url in _INITIALIZED_DATABASES:
                return
            ensure_database_directory(database_url)
            if self._use_sync_sqlite:
                if self._sync_engine is None:
                    raise RuntimeError("Expected synchronous SQLite engine to be configured.")
                with self._sync_engine.begin() as connection:
                    Base.metadata.create_all(connection)
            else:
                if self._async_engine is None:
                    raise RuntimeError("Expected async engine to be configured.")
                async with self._async_engine.begin() as connection:
                    await connection.run_sync(Base.metadata.create_all)
            _INITIALIZED_DATABASES.add(database_url)

    def session(self) -> AsyncSession | AsyncSessionAdapter:
        if self._use_sync_sqlite:
            if self._sync_session_factory is None:
                raise RuntimeError("Expected synchronous SQLite session factory to exist.")
            return AsyncSessionAdapter(self._sync_session_factory())
        if self._async_session_factory is None:
            raise RuntimeError("Expected async session factory to exist.")
        return self._async_session_factory()

    async def close(self) -> None:
        if self._sync_engine is not None:
            self._sync_engine.dispose()
        if self._async_engine is not None:
            await self._async_engine.dispose()
