"""SQLAlchemy ORM table definitions (SQLite)."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(256), unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="user")

    runs: Mapped[list[Run]] = relationship(back_populates="user")
    loader_draft: Mapped[LoaderDraftRow | None] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )


class LoaderDraftRow(Base):
    """One durable loader draft per app user (Wave D)."""

    __tablename__ = "loader_drafts"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    draft_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String(64))
    last_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_run_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped[User] = relationship(back_populates="loader_draft")


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    mt_org_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mt_org_label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    config_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[str] = mapped_column(String(64))
    completed_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resources_created_count: Mapped[int] = mapped_column(Integer, default=0)
    resources_staged_count: Mapped[int] = mapped_column(Integer, default=0)
    resources_failed_count: Mapped[int] = mapped_column(Integer, default=0)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_extras_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User | None] = relationship(back_populates="runs")
    created_resources: Mapped[list[RunCreatedResource]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    resource_failures: Mapped[list[RunResourceFailure]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    staged_items: Mapped[list[RunStagedItem]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class RunCreatedResource(Base):
    __tablename__ = "run_created_resources"

    created_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False
    )
    batch: Mapped[int] = mapped_column(Integer)
    resource_type: Mapped[str] = mapped_column(String(128))
    typed_ref: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[str] = mapped_column(String(64))
    deletable: Mapped[bool] = mapped_column(Boolean, default=False)
    cleanup_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    child_refs_json: Mapped[str] = mapped_column(Text, default="{}")

    run: Mapped[Run] = relationship(back_populates="created_resources")


class RunResourceFailure(Base):
    __tablename__ = "run_resource_failures"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False
    )
    typed_ref: Mapped[str] = mapped_column(String(512))
    error: Mapped[str] = mapped_column(Text)
    failed_at: Mapped[str] = mapped_column(String(64))
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_cause: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[Run] = relationship(back_populates="resource_failures")


class RunStagedItem(Base):
    __tablename__ = "run_staged_items"

    run_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    typed_ref: Mapped[str] = mapped_column(String(512), primary_key=True)
    resource_type: Mapped[str] = mapped_column(String(128))
    staged_at: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)

    run: Mapped[Run] = relationship(back_populates="staged_items")


class WebhookEvent(Base):
    """Append-only inbound webhook receipt (Wave C). ``run_id`` nullable = unmatched."""

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    webhook_id: Mapped[str | None] = mapped_column(String(256), unique=True, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    typed_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    received_at: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(256))
    resource_type: Mapped[str] = mapped_column(String(128))
    resource_id: Mapped[str] = mapped_column(String(256), default="")
    raw_json: Mapped[str] = mapped_column(Text)
