from datetime import datetime, timedelta
from typing import Annotated, Literal, get_args

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import String, and_, func, literal, or_, select, true, union_all

from .audit import AuditLog
from .catalog import Market
from .db import DB, utcnow
from .fulfillment import Provision, Reservation
from .identity import Operator
from .procurement import Contract, Dispute, Order, PaymentReceipt, Refund
from .token import Usage

router = APIRouter(tags=["Operations"])
ExceptionKind = Literal[
    "PENDING_CONTRACT", "PENDING_START", "PENDING_PROVISION", "PENDING_COMPLETION",
    "PENDING_RECEIPT", "EXPIRED_RESERVATION", "OPEN_DISPUTE",
    "PENDING_REFUND", "TOKEN_REVIEW", "FULFILLMENT_OVERDUE",
]


class OrderException(BaseModel):
    order_id: str
    market: Market
    product_name: str
    order_state: str
    kind: ExceptionKind
    opened_at: AwareDatetime
    record_id: str | None


class OrderExceptionPage(BaseModel):
    items: list[OrderException]
    total: int
    counts: dict[ExceptionKind, int]


def exception_queue(now: datetime):
    no_record_id = literal(None, type_=String(36)).label("record_id")
    last_transition = select(func.max(AuditLog.created_at)).where(
        AuditLog.entity_type == "order", AuditLog.entity_id == Order.id,
        AuditLog.details["to_state"].as_string() == Order.state,
    ).scalar_subquery()
    state_entered_at = func.coalesce(last_transition, Order.created_at)
    order_refund = select(Refund.id).where(Refund.order_id == Order.id)
    unprovisioned = ~select(Provision.order_id).where(Provision.order_id == Order.id).exists()
    return union_all(
        select(Order.id.label("order_id"), literal("PENDING_CONTRACT").label("kind"),
               Order.created_at.label("opened_at"), no_record_id)
        .where(Order.state == "CONTRACT_PENDING",
               ~select(Contract.order_id).where(Contract.order_id == Order.id).exists()),
        select(Order.id, literal("PENDING_START"), state_entered_at, no_record_id)
        .where(Order.state == "PAID", ~order_refund.where(Refund.state == "APPROVED").exists()),
        select(Order.id, literal("PENDING_PROVISION"), state_entered_at, no_record_id)
        .outerjoin(Reservation, Reservation.order_id == Order.id)
        .where(Order.market == "COMPUTE", Order.state == "FULFILLING", unprovisioned,
               or_(and_(Reservation.status == "CONFIRMED", Reservation.starts_at > now),
                   and_(Reservation.order_id.is_(None), Order.snapshot["compute_schedule"].as_string().is_(None)))),
        select(Order.id, literal("PENDING_COMPLETION"), state_entered_at, no_record_id)
        .where(Order.state == "ACCEPTED", ~order_refund.where(Refund.state.in_(("REQUESTED", "APPROVED"))).exists()),
        select(PaymentReceipt.order_id, literal("PENDING_RECEIPT").label("kind"),
               PaymentReceipt.submitted_at.label("opened_at"), PaymentReceipt.id.label("record_id"))
        .join(Order, Order.id == PaymentReceipt.order_id)
        .where(PaymentReceipt.state == "PENDING", Order.state == "PAYMENT_PENDING"),
        select(Reservation.order_id, literal("EXPIRED_RESERVATION"), Reservation.expires_at, no_record_id)
        .join(Order, Order.id == Reservation.order_id)
        .where(Reservation.status == "HELD", Reservation.expires_at <= now,
               Order.state.in_(("CONTRACT_PENDING", "PAYMENT_PENDING"))),
        select(Dispute.order_id, literal("OPEN_DISPUTE"), Dispute.created_at, Dispute.id)
        .where(Dispute.resolved_at.is_(None)),
        select(Refund.order_id, literal("PENDING_REFUND"), Refund.created_at, Refund.id)
        .where(Refund.state.in_(("REQUESTED", "APPROVED"))),
        select(Usage.order_id, literal("TOKEN_REVIEW"), Usage.created_at, Usage.id)
        .where(or_(Usage.status == "REVIEW_REQUIRED", and_(
            Usage.status == "PENDING", Usage.created_at < now - timedelta(minutes=2),
        ))),
        select(Reservation.order_id, literal("FULFILLMENT_OVERDUE"), Reservation.starts_at, no_record_id)
        .join(Order, Order.id == Reservation.order_id)
        .where(Reservation.status == "CONFIRMED", Reservation.starts_at <= now,
               Order.market == "COMPUTE", Order.state.in_(("PAID", "FULFILLING")),
               ~select(Provision.order_id).where(Provision.order_id == Reservation.order_id).exists()),
    ).cte("order_exceptions")


@router.get("/admin/orders/exception-list", response_model=OrderExceptionPage)
def list_exceptions(
    db: DB, _operator: Operator, kind: ExceptionKind | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> OrderExceptionPage:
    queue = exception_queue(utcnow())
    counts = select(queue.c.kind, func.count().label("total")).group_by(queue.c.kind).subquery()
    stats = select(func.json_object_agg(counts.c.kind, counts.c.total).label("counts")).subquery()
    page = select(
        Order.id.label("order_id"), Order.market, Order.state.label("order_state"),
        func.coalesce(Order.snapshot["name"].as_string(), Order.id).label("product_name"),
        queue.c.kind, queue.c.opened_at, queue.c.record_id,
    ).join(queue, queue.c.order_id == Order.id)
    if kind is not None:
        page = page.where(queue.c.kind == kind)
    page = page.order_by(queue.c.opened_at, queue.c.order_id, queue.c.kind, queue.c.record_id).limit(limit).offset(offset).subquery()

    # The outer join keeps the counts available even when the requested page is empty.
    rows = db.execute(select(page, stats.c.counts).select_from(stats.outerjoin(page, true()))
                      .order_by(page.c.opened_at, page.c.order_id, page.c.kind, page.c.record_id)).mappings().all()
    totals = dict.fromkeys(get_args(ExceptionKind), 0)
    totals.update(rows[0]["counts"] or {})
    return OrderExceptionPage(
        items=[OrderException.model_validate(row) for row in rows if row["order_id"] is not None],
        total=totals[kind] if kind else sum(totals.values()), counts=totals,
    )
