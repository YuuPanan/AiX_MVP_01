from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, DB, utcnow
from .identity import CurrentUser, Membership, Organization, User
from .procurement import BUYERS, SELLERS, RFQ, get_rfq

router = APIRouter(tags=["RFQ messages"])
SenderSide = Literal["BUYER", "SUPPLIER", "OPERATOR"]
MAX_CURSOR = 2**63 - 1


class RFQMessage(Base):
    __tablename__ = "rfq_messages"
    __table_args__ = (
        CheckConstraint("char_length(body) BETWEEN 1 AND 2000", name="ck_rfq_message_body_length"),
        CheckConstraint("sender_side IN ('BUYER', 'SUPPLIER', 'OPERATOR')", name="ck_rfq_message_sender_side"),
        Index("ix_rfq_messages_thread_cursor", "rfq_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rfq_id: Mapped[str] = mapped_column(ForeignKey("rfqs.id"))
    sender_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    sender_side: Mapped[str] = mapped_column(String(8))
    sender_name: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2000, pattern=r"^[^\x00]*$"),
    ]


class MessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    body: str
    sender_side: SenderSide
    sender_name: str
    created_at: datetime


class MessagePage(BaseModel):
    messages: list[MessageView]
    next_before_id: int | None
    can_send: bool


def write_parties(
    db: Session, user: User, rfq: RFQ, organization_id: str | None,
) -> list[tuple[SenderSide, str | None]]:
    if user.is_operator:
        return [("OPERATOR", None)]
    parties = []
    for side, party_id, roles in (
        ("BUYER", rfq.buyer_organization_id, BUYERS),
        ("SUPPLIER", rfq.supplier_organization_id, SELLERS),
    ):
        if organization_id is not None and organization_id != party_id:
            continue
        member = db.get(Membership, (party_id, user.id))
        if member is not None and member.role in roles:
            parties.append((side, party_id))
    return parties


@router.get("/rfqs/{rfq_id}/messages", response_model=MessagePage)
def list_messages(
    rfq_id: str, db: DB, user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before_id: Annotated[int | None, Query(ge=1, le=MAX_CURSOR)] = None,
    organization_id: str | None = None,
):
    rfq = get_rfq(db, user, rfq_id, organization_id=organization_id)
    query = select(RFQMessage).where(RFQMessage.rfq_id == rfq.id)
    if before_id is not None:
        query = query.where(RFQMessage.id < before_id)
    rows = db.scalars(query.order_by(RFQMessage.id.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    return MessagePage(
        messages=[MessageView.model_validate(row) for row in reversed(page)],
        next_before_id=page[-1].id if len(rows) > limit else None,
        can_send=len(write_parties(db, user, rfq, organization_id)) == 1,
    )


@router.post("/rfqs/{rfq_id}/messages", response_model=MessageView, status_code=201)
def send_message(rfq_id: str, payload: MessageInput, db: DB, user: CurrentUser, organization_id: str | None = None):
    # Serialize inserts within a thread so a newer cursor cannot commit before an older one.
    rfq = get_rfq(db, user, rfq_id, lock=True, organization_id=organization_id)
    parties = write_parties(db, user, rfq, organization_id)
    if not parties:
        raise HTTPException(403, "Only the buying team, supplying team or operator may send messages")
    if len(parties) != 1:
        raise HTTPException(409, "Account belongs to both parties and has no unambiguous sending organization")
    side, organization_id = parties[0]
    name = db.get(Organization, organization_id).name if organization_id is not None else "运营"
    message = RFQMessage(
        rfq_id=rfq.id, sender_user_id=user.id, sender_side=side, sender_name=name, body=payload.body,
    )
    db.add(message)
    db.flush()
    view = MessageView.model_validate(message)
    db.commit()
    return view
