"""SQLAlchemy ORM table definitions (SQLite)."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
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
    manifest_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User | None] = relationship(back_populates="runs")
    correlations: Mapped[list[ResourceCorrelation]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


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


class ResourceCorrelation(Base):
    __tablename__ = "resource_correlation"

    created_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), ForeignKey("runs.run_id", ondelete="CASCADE"))
    typed_ref: Mapped[str] = mapped_column(String(512))

    run: Mapped[Run] = relationship(back_populates="correlations")
