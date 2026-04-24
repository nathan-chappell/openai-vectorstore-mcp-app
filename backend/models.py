from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid4().hex


class Base(DeclarativeBase):
    """Declarative SQLAlchemy base for app-owned state."""


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    clerk_user_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    primary_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(nullable=False, default=False)
    role: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    file_library: Mapped["FileLibrary | None"] = relationship(
        back_populates="owner",
        uselist=False,
    )
    uploaded_files: Mapped[list["LibraryFile"]] = relationship(back_populates="uploaded_by")
    chat_threads: Mapped[list["AppChatThread"]] = relationship(back_populates="user")
    chat_attachments: Mapped[list["AppChatAttachment"]] = relationship(back_populates="user")


class FileLibrary(Base):
    __tablename__ = "file_library"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_file_library_user_id"),
        Index("ix_file_library_user_id_updated_at", "user_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    openai_vector_store_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        unique=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    owner: Mapped[AppUser] = relationship(back_populates="file_library")
    files: Mapped[list["LibraryFile"]] = relationship(
        back_populates="file_library",
        cascade="all, delete-orphan",
    )
    tags: Mapped[list["FileTag"]] = relationship(
        back_populates="file_library",
        cascade="all, delete-orphan",
    )


class FileTag(Base):
    __tablename__ = "file_tag"
    __table_args__ = (
        UniqueConstraint("file_library_id", "slug", name="uq_file_tag_slug"),
        UniqueConstraint("file_library_id", "name", name="uq_file_tag_name"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    file_library_id: Mapped[str] = mapped_column(
        ForeignKey("file_library.id"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(96), nullable=False)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    file_library: Mapped[FileLibrary] = relationship(back_populates="tags")
    file_links: Mapped[list["FileTagLink"]] = relationship(
        back_populates="tag",
        cascade="all, delete-orphan",
    )


class LibraryFile(Base):
    __tablename__ = "library_file"
    __table_args__ = (
        Index(
            "ix_library_file_library_status_created_at",
            "file_library_id",
            "status",
            "created_at",
        ),
        UniqueConstraint(
            "file_library_id",
            "display_title",
            name="uq_library_file_display_title",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    file_library_id: Mapped[str] = mapped_column(
        ForeignKey("file_library.id"),
        nullable=False,
        index=True,
    )
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id"),
        nullable=True,
        index=True,
    )
    display_title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    byte_size: Mapped[int] = mapped_column(nullable=False, default=0)
    original_mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    openai_original_file_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        unique=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    file_library: Mapped[FileLibrary] = relationship(back_populates="files")
    uploaded_by: Mapped[AppUser | None] = relationship(back_populates="uploaded_files")
    derived_artifacts: Mapped[list["DerivedArtifact"]] = relationship(
        back_populates="library_file",
        cascade="all, delete-orphan",
    )
    tag_links: Mapped[list["FileTagLink"]] = relationship(
        back_populates="library_file",
        cascade="all, delete-orphan",
    )


class DerivedArtifact(Base):
    __tablename__ = "derived_artifact"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(
        ForeignKey("library_file.id"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    openai_file_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    structured_payload: Mapped[dict[str, object] | list[object] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    library_file: Mapped[LibraryFile] = relationship(back_populates="derived_artifacts")


class FileTagLink(Base):
    __tablename__ = "file_tag_link"

    file_id: Mapped[str] = mapped_column(ForeignKey("library_file.id"), primary_key=True)
    tag_id: Mapped[str] = mapped_column(ForeignKey("file_tag.id"), primary_key=True)

    library_file: Mapped[LibraryFile] = relationship(back_populates="tag_links")
    tag: Mapped[FileTag] = relationship(back_populates="file_links")


class AppChatThread(Base):
    __tablename__ = "app_chat_thread"
    __table_args__ = (Index("ix_app_chat_thread_user_updated_sequence", "user_id", "updated_sequence"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    status_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    allowed_image_domains_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    updated_sequence: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped[AppUser] = relationship(back_populates="chat_threads")
    entries: Mapped[list["AppChatEntry"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
    )


class AppChatEntry(Base):
    __tablename__ = "app_chat_entry"
    __table_args__ = (
        UniqueConstraint("thread_id", "sequence", name="uq_app_chat_entry_thread_sequence"),
        Index("ix_app_chat_entry_thread_sequence", "thread_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("app_chat_thread.id"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    item_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    thread: Mapped[AppChatThread] = relationship(back_populates="entries")


class AppChatAttachment(Base):
    __tablename__ = "app_chat_attachment"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped[AppUser | None] = relationship(back_populates="chat_attachments")
