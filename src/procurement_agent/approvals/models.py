"""Tables owned by this app. ERPNext's database is not touched."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProposalRow(Base):
    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    action: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    erp_document_name: Mapped[str | None] = mapped_column(String(140), nullable=True)
    slack_channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    slack_ts: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id"))
    event: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(128))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
