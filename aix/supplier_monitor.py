from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import func, literal, select, union_all

from .audit import AuditLog
from .catalog import Product
from .db import DB, utcnow
from .fulfillment import Provision, ProvisionView, Reservation, ReservationView, SLAEvent, reservation_status
from .identity import CurrentUser, require_role
from .procurement import Order, Payment, RFQ, SELLERS, ShortText, StatementView, order_refunds, statement

router = APIRouter(prefix="/organizations/{organization_id}", tags=["Supplier monitor"])
Category = Literal["ORDERS", "QUOTES", "SYSTEM"]
ORDER_ACTIONS = (
    "order.created", "contract.archived", "contract.confirmed", "payment.awaiting_verification",
    "payment.verified", "fulfillment.started", "COMPUTE_PROVISIONED", "order.delivered", "delivery.accepted",
    "order.completed", "dispute.opened", "dispute.resolved", "refund.requested", "refund.reviewed", "refund.verified",
)
QUOTE_ACTIONS = ("rfq.submitted", "rfq.declined", "quote.sent", "quote.accepted")
SYSTEM_ACTIONS = (
    "COMPUTE_RESERVATION_HELD", "COMPUTE_RESERVATION_CONFIRMED", "COMPUTE_RESERVATION_RELEASED", "COMPUTE_SLA_EVENT",
)
PageLimit = Annotated[int, Query(ge=1, le=100)]


class SupplierOrder(BaseModel):
    id: str
    rfq_id: str
    market: str
    state: str
    product_name: str
    buyer_name: str
    currency: str
    amount: Decimal
    created_at: AwareDatetime


class SupplierEvent(BaseModel):
    id: int
    action: str
    created_at: AwareDatetime
    to_state: str | None
    event_kind: str | None


class SLAActivity(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: Literal["OUTAGE", "RECOVERY", "SLA_BREACH"]
    occurred_at: AwareDatetime


class SupplierOrderMonitor(SupplierOrder):
    reservation: ReservationView | None
    provisioning: ProvisionView | None
    statement: StatementView
    sla_events: list[SLAActivity]
    events: list[SupplierEvent]


class SupplierNotification(SupplierEvent):
    category: Category
    target_type: Literal["order", "rfq"]
    target_id: str
    product_name: str


class SupplierNotifications(BaseModel):
    items: list[SupplierNotification]
    next_before_id: int | None


def supplier_order(order: Order) -> SupplierOrder:
    return SupplierOrder(
        id=order.id, rfq_id=order.rfq_id, market=order.market, state=order.state,
        product_name=order.snapshot.get("name") or order.id,
        buyer_name=order.snapshot.get("buyer_organization_name") or order.buyer_organization_id,
        currency=order.currency, amount=order.amount, created_at=order.created_at,
    )


def event_columns():
    return (
        AuditLog.id, AuditLog.action, AuditLog.created_at,
        AuditLog.details["to_state"].as_string().label("to_state"),
        AuditLog.details["kind"].as_string().label("event_kind"),
    )


@router.get("/supplier-orders", response_model=list[SupplierOrder])
def list_supplier_orders(
    organization_id: str, db: DB, user: CurrentUser, state: ShortText | None = None,
    limit: PageLimit = 25, offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
):
    require_role(db, user, organization_id, *SELLERS)
    query = select(Order).where(Order.supplier_organization_id == organization_id)
    if state is not None:
        query = query.where(Order.state == state)
    rows = db.scalars(query.order_by(Order.created_at.desc(), Order.id).limit(limit).offset(offset))
    return [supplier_order(order) for order in rows]


@router.get("/supplier-orders/{order_id}", response_model=SupplierOrderMonitor)
def read_supplier_order(organization_id: str, order_id: UUID, db: DB, user: CurrentUser) -> SupplierOrderMonitor:
    require_role(db, user, organization_id, *SELLERS)
    row = db.execute(select(Order, Reservation, Provision, Payment)
                     .outerjoin(Reservation, Reservation.order_id == Order.id)
                     .outerjoin(Provision, Provision.order_id == Order.id)
                     .outerjoin(Payment, Payment.order_id == Order.id)
                     .where(Order.id == str(order_id), Order.supplier_organization_id == organization_id)).one_or_none()
    if row is None:
        raise HTTPException(404, "Supplier order not found")
    order, reservation, provision, payment = row
    events = db.execute(select(*event_columns()).where(
        AuditLog.entity_type == "order", AuditLog.entity_id == order.id,
        AuditLog.action.in_((*ORDER_ACTIONS, *SYSTEM_ACTIONS)),
    ).order_by(AuditLog.id.desc()).limit(25)).mappings()
    sla_events = db.scalars(select(SLAEvent).where(SLAEvent.order_id == order.id)
                           .order_by(SLAEvent.occurred_at.desc(), SLAEvent.id).limit(25))
    return SupplierOrderMonitor(
        **supplier_order(order).model_dump(),
        reservation=ReservationView.model_validate(reservation).model_copy(
            update={"status": reservation_status(reservation, utcnow())},
        ) if reservation else None,
        provisioning=ProvisionView.model_validate(provision, from_attributes=True) if provision else None,
        statement=statement(order, payment, order_refunds(db, order.id)),
        sla_events=[SLAActivity.model_validate(event) for event in sla_events],
        events=[SupplierEvent.model_validate(event) for event in events],
    )


@router.get("/supplier-notifications", response_model=SupplierNotifications)
def list_supplier_notifications(
    organization_id: str, db: DB, user: CurrentUser, category: Category | None = None, limit: PageLimit = 25,
    before_id: Annotated[int, Query(ge=1, le=2**31 - 1)] | None = None,
) -> SupplierNotifications:
    require_role(db, user, organization_id, *SELLERS)
    queries = []
    for name, actions in (("ORDERS", ORDER_ACTIONS), ("SYSTEM", SYSTEM_ACTIONS)):
        if category is None or category == name:
            queries.append(select(
                *event_columns(), literal(name).label("category"), literal("order").label("target_type"),
                Order.id.label("target_id"), func.coalesce(Order.snapshot["name"].as_string(), Order.id).label("product_name"),
            ).join(Order, Order.id == AuditLog.entity_id).where(
                AuditLog.entity_type == "order", AuditLog.action.in_(actions),
                Order.supplier_organization_id == organization_id,
            ))
    if category in (None, "QUOTES"):
        queries.append(select(
            *event_columns(), literal("QUOTES").label("category"), literal("rfq").label("target_type"),
            RFQ.id.label("target_id"), Product.name.label("product_name"),
        ).join(RFQ, RFQ.id == AuditLog.entity_id).join(Product, Product.id == RFQ.product_id).where(
            AuditLog.entity_type == "rfq", AuditLog.action.in_(QUOTE_ACTIONS),
            RFQ.supplier_organization_id == organization_id,
        ))
    if before_id is not None:
        queries = [query.where(AuditLog.id < before_id) for query in queries]
    feed = (union_all(*queries) if len(queries) > 1 else queries[0]).subquery()
    rows = db.execute(select(feed).order_by(feed.c.id.desc()).limit(limit + 1)).mappings().all()
    return SupplierNotifications(
        items=[SupplierNotification.model_validate(row) for row in rows[:limit]],
        next_before_id=rows[limit - 1]["id"] if len(rows) > limit else None,
    )
