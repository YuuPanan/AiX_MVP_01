from contextvars import ContextVar
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, utcnow

if TYPE_CHECKING:
    from .identity import User


request_context: ContextVar[tuple[str | None, str | None]] = ContextVar(
    "request_context", default=(None, None)
)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    request_id: Mapped[str | None] = mapped_column(String(36))
    source_ip: Mapped[str | None] = mapped_column(String(45))


def audit(
    db: Session,
    actor: "User | None",
    entity_type: str,
    entity_id: str,
    action: str,
    details: dict | None = None,
) -> None:
    request_id, source_ip = request_context.get()
    db.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            details={} if details is None else details,
            request_id=request_id,
            source_ip=source_ip,
        )
    )
