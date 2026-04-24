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

    knowledge_base: Mapped["KnowledgeBase | None"] = relationship(
        back_populates="owner",
        uselist=False,
    )
    created_nodes: Mapped[list["KnowledgeNode"]] = relationship(back_populates="created_by")
    chat_threads: Mapped[list["AppChatThread"]] = relationship(back_populates="user")
    chat_attachments: Mapped[list["AppChatAttachment"]] = relationship(back_populates="user")


class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_knowledge_base_user_id"),
        Index("ix_knowledge_base_user_id_updated_at", "user_id", "updated_at"),
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
    openai_conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
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

    owner: Mapped[AppUser] = relationship(back_populates="knowledge_base")
    nodes: Mapped[list["KnowledgeNode"]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )
    tags: Mapped[list["KnowledgeTag"]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )
    edges: Mapped[list["KnowledgeEdge"]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )


class KnowledgeTag(Base):
    __tablename__ = "knowledge_tag"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "slug", name="uq_knowledge_tag_slug"),
        UniqueConstraint("knowledge_base_id", "name", name="uq_knowledge_tag_name"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_base.id"),
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

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="tags")
    node_links: Mapped[list["KnowledgeNodeTag"]] = relationship(
        back_populates="tag",
        cascade="all, delete-orphan",
    )


class KnowledgeNode(Base):
    __tablename__ = "knowledge_node"
    __table_args__ = (
        Index(
            "ix_knowledge_node_kb_status_created_at",
            "knowledge_base_id",
            "status",
            "created_at",
        ),
        UniqueConstraint(
            "knowledge_base_id",
            "display_title",
            name="uq_knowledge_node_display_title",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_base.id"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
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

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="nodes")
    created_by: Mapped[AppUser | None] = relationship(back_populates="created_nodes")
    derived_artifacts: Mapped[list["DerivedArtifact"]] = relationship(
        back_populates="node",
        cascade="all, delete-orphan",
    )
    tag_links: Mapped[list["KnowledgeNodeTag"]] = relationship(
        back_populates="node",
        cascade="all, delete-orphan",
    )
    outgoing_edges: Mapped[list["KnowledgeEdge"]] = relationship(
        back_populates="from_node",
        cascade="all, delete-orphan",
        foreign_keys="KnowledgeEdge.from_node_id",
    )
    incoming_edges: Mapped[list["KnowledgeEdge"]] = relationship(
        back_populates="to_node",
        cascade="all, delete-orphan",
        foreign_keys="KnowledgeEdge.to_node_id",
    )


class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edge"
    __table_args__ = (
        UniqueConstraint("from_node_id", "to_node_id", name="uq_knowledge_edge_nodes"),
        Index("ix_knowledge_edge_kb_from", "knowledge_base_id", "from_node_id"),
        Index("ix_knowledge_edge_kb_to", "knowledge_base_id", "to_node_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_base.id"),
        nullable=False,
        index=True,
    )
    from_node_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_node.id"),
        nullable=False,
        index=True,
    )
    to_node_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_node.id"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
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

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="edges")
    from_node: Mapped[KnowledgeNode] = relationship(
        back_populates="outgoing_edges",
        foreign_keys=[from_node_id],
    )
    to_node: Mapped[KnowledgeNode] = relationship(
        back_populates="incoming_edges",
        foreign_keys=[to_node_id],
    )


class DerivedArtifact(Base):
    __tablename__ = "derived_artifact"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_node.id"),
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

    node: Mapped[KnowledgeNode] = relationship(back_populates="derived_artifacts")


class KnowledgeNodeTag(Base):
    __tablename__ = "knowledge_node_tag"

    node_id: Mapped[str] = mapped_column(ForeignKey("knowledge_node.id"), primary_key=True)
    tag_id: Mapped[str] = mapped_column(ForeignKey("knowledge_tag.id"), primary_key=True)

    node: Mapped[KnowledgeNode] = relationship(back_populates="tag_links")
    tag: Mapped[KnowledgeTag] = relationship(back_populates="node_links")


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
