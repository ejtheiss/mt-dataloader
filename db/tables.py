"""SQLAlchemy ORM table definitions (SQLite)."""

from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(256), unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    runs: Mapped[list[Run]] = relationship(back_populates="user")


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

    user: Mapped[User | None] = relationship(back_populates="runs")
    correlations: Mapped[list[ResourceCorrelation]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class ResourceCorrelation(Base):
    __tablename__ = "resource_correlation"

    created_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), ForeignKey("runs.run_id", ondelete="CASCADE"))
    typed_ref: Mapped[str] = mapped_column(String(512))

    run: Mapped[Run] = relationship(back_populates="correlations")
