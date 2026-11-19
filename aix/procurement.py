from csv import writer
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from io import StringIO
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import JSON, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from .audit import AuditLog, audit
from .catalog import Product, Supplier, get_published_product
from .db import DB, Base, utcnow
from .identity import CurrentUser, Membership, Operator, Organization, User, require_role

router = APIRouter(tags=["procurement"])
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
BUYERS = ("OWNER", "BUYER")
SELLERS = ("OWNER", "SUPPLIER")
DISCRETE_UNITS = {"INPUT_TOKEN", "OUTPUT_TOKEN", "TOKEN", "REQUEST", "IMAGE", "INSTANCE"}
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160, pattern=r"^[^\x00]+$")]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=3000, pattern=r"^[^\x00]+$")]
OptionalNotes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=3000, pattern=r"^[^\x00]*$")]
Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500, pattern=r"^[^\x00]+$")]
Checksum = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Quantity = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3)]
UnitPrice = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=8)]
Amount = Annotated[Decimal, Field(gt=0, max_digits=23, decimal_places=2)]
Fees = Annotated[Decimal, Field(ge=0, max_digits=23, decimal_places=2)]
Currency = Literal["USD", "CNY"]
RFQState = Literal["SUBMITTED", "QUOTED", "ACCEPTED", "REJECTED"]


class RFQ(Base):
    __tablename__ = "rfqs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    buyer_organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    supplier_organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    requirements: Mapped[str] = mapped_column(String(3000))
    compute_schedule: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    state: Mapped[str] = mapped_column(String(24), default="SUBMITTED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    rfq_id: Mapped[str] = mapped_column(ForeignKey("rfqs.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(23, 2))
    snapshot: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(24), default="SENT")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    buyer_organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    supplier_organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    rfq_id: Mapped[str] = mapped_column(ForeignKey("rfqs.id"), unique=True)
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"), unique=True)
    market: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(24), default="CONTRACT_PENDING")
    snapshot: Mapped[dict] = mapped_column(JSON)
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(Numeric(23, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Contract(Base):
    __tablename__ = "order_contracts"

    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    version: Mapped[str] = mapped_column(String(160))
    evidence_ref: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    payment_instructions: Mapped[str] = mapped_column(String(3000))
    buyer_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supplier_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Payment(Base):
    __tablename__ = "order_payments"
    __table_args__ = (UniqueConstraint("channel", "external_reference"),)

    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    channel: Mapped[str] = mapped_column(String(160))
    external_reference: Mapped[str] = mapped_column(String(160))
    amount: Mapped[Decimal] = mapped_column(Numeric(23, 2))
    currency: Mapped[str] = mapped_column(String(3))
    evidence_ref: Mapped[str] = mapped_column(String(500))
    verified_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    bank_credited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentReceipt(Base):
    __tablename__ = "order_payment_receipts"
    __table_args__ = (
        Index("uq_order_payment_receipts_pending", "order_id", unique=True, postgresql_where=text("state = 'PENDING'")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    attachment_id: Mapped[str] = mapped_column(ForeignKey("attachments.id"))
    channel: Mapped[str] = mapped_column(String(160))
    external_reference: Mapped[str] = mapped_column(String(160))
    amount: Mapped[Decimal] = mapped_column(Numeric(23, 2))
    currency: Mapped[str] = mapped_column(String(3))
    state: Mapped[str] = mapped_column(String(16), default="PENDING")
    submitted_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(String(3000))

    @property
    def evidence_ref(self) -> str:
        return f"attachment:{self.attachment_id}"


class Dispute(Base):
    __tablename__ = "order_disputes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    previous_order_state: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(String(3000))
    decision: Mapped[str | None] = mapped_column(String(3000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Refund(Base):
    __tablename__ = "order_refunds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(23, 2))
    reason: Mapped[str] = mapped_column(String(3000))
    state: Mapped[str] = mapped_column(String(24), default="REQUESTED")
    external_reference: Mapped[str | None] = mapped_column(String(160), unique=True)
    evidence_ref: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ComputeScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gpu_count: Annotated[int, Field(strict=True, ge=1, le=10_000)]
    starts_at: AwareDatetime
    duration_hours: Annotated[int, Field(strict=True, ge=1, le=2160)]


class ComputeScheduleView(ComputeScheduleInput):
    ends_at: AwareDatetime


class RFQInput(BaseModel):
    organization_id: ShortText
    product_id: ShortText
    quantity: Quantity | None = None
    requirements: OptionalNotes | None = None
    compute_schedule: ComputeScheduleInput | None = None


class RFQView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    buyer_organization_id: str
    supplier_organization_id: str
    product_id: str
    quantity: Decimal
    requirements: str
    compute_schedule: ComputeScheduleView | None
    state: str
    created_at: datetime


class RFQDeclineInput(BaseModel):
    reason: Notes


class QuoteInput(BaseModel):
    unit_price: UnitPrice
    expires_at: AwareDatetime
    terms: Notes
    tax_and_fees: Fees = Decimal("0.00")


class QuoteView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    rfq_id: str
    amount: Decimal
    snapshot: dict
    state: str
    expires_at: datetime
    created_at: datetime


class OrderView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    buyer_organization_id: str
    supplier_organization_id: str
    supplier_id: str
    rfq_id: str
    quote_id: str
    market: str
    state: str
    snapshot: dict
    currency: str
    amount: Decimal
    created_at: datetime


class ContractInput(BaseModel):
    version: ShortText
    evidence_ref: Reference
    sha256: Checksum
    payment_instructions: Notes


class ContractView(ContractInput):
    model_config = ConfigDict(from_attributes=True)

    payment_instructions: Notes | None
    buyer_confirmed_at: datetime | None
    supplier_confirmed_at: datetime | None


class ContractConfirmation(BaseModel):
    organization_id: ShortText


class PaymentInput(BaseModel):
    channel: ShortText
    external_reference: ShortText
    amount: Amount
    currency: Currency
    evidence_ref: Reference


class PaymentView(PaymentInput):
    model_config = ConfigDict(from_attributes=True)

    external_reference: ShortText | None
    evidence_ref: Reference | None
    verified_by: str
    verified_at: datetime
    bank_credited_at: datetime | None


class PaymentReceiptView(PaymentInput):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_id: str
    attachment_id: str
    state: Literal["PENDING", "APPROVED", "REJECTED"]
    submitted_by: str
    submitted_at: datetime
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None


class BankCreditInput(PaymentInput):
    model_config = ConfigDict(extra="forbid")

    credited_at: AwareDatetime


class PaymentReceiptReview(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    note: Notes
    bank_credit: BankCreditInput | None = None


class EvidenceInput(BaseModel):
    evidence_ref: Reference
    summary: Notes


class AcceptanceInput(BaseModel):
    note: Notes | None = None


class DisputeInput(BaseModel):
    reason: Notes


class DisputeResolution(BaseModel):
    decision: Notes
    redeliver: bool = False


class DisputeView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    reason: str
    decision: str | None
    created_at: datetime
    resolved_at: datetime | None


class RefundInput(BaseModel):
    amount: Amount
    reason: Notes


class RefundReview(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    note: Notes


class RefundConfirmation(BaseModel):
    external_reference: ShortText
    evidence_ref: Reference


class RefundView(RefundInput):
    model_config = ConfigDict(from_attributes=True)

    id: str
    state: str
    external_reference: str | None
    evidence_ref: str | None
    created_at: datetime


class StatementView(BaseModel):
    order_id: str
    order_state: str
    currency: str
    amount: Decimal
    verified_paid: Decimal
    verified_refunded: Decimal
    payment_status: str


class ComputeReservationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: Literal["HELD", "CONFIRMED", "EXPIRED", "RELEASED", "PROVISIONED"]
    expires_at: AwareDatetime


class OrderDetail(OrderView):
    contract: ContractView | None
    payment: PaymentView | None
    statement: StatementView
    disputes: list[DisputeView]
    refunds: list[RefundView]
    compute_reservation: ComputeReservationSummary | None


class TimelineEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_id: str | None
    action: str
    details: dict | None
    created_at: datetime
    request_id: str | None


def tenant_scope(model: type[Order] | type[RFQ], user: User, organization_id: str | None = None):
    buyer_orgs = select(Membership.organization_id).where(
        Membership.user_id == user.id, Membership.role.in_((*BUYERS, "FINANCE"))
    )
    supplier_orgs = select(Membership.organization_id).where(
        Membership.user_id == user.id, Membership.role.in_((*SELLERS, "FINANCE"))
    )
    if organization_id is not None:
        buyer_orgs = buyer_orgs.where(Membership.organization_id == organization_id)
        supplier_orgs = supplier_orgs.where(Membership.organization_id == organization_id)
    return or_(model.buyer_organization_id.in_(buyer_orgs), model.supplier_organization_id.in_(supplier_orgs))


def visible_to(query, model: type[Order] | type[RFQ], user: User, organization_id: str | None = None):
    if not user.is_operator:
        return query.where(tenant_scope(model, user, organization_id))
    if organization_id is not None:
        query = query.where(or_(
            model.buyer_organization_id == organization_id,
            model.supplier_organization_id == organization_id,
        ))
    return query


def validate_quantity(quantity: Decimal, unit: str) -> None:
    if unit in DISCRETE_UNITS and quantity != quantity.to_integral_value():
        raise HTTPException(422, f"{unit} requires a whole number quantity")


def rfq_quantity_and_schedule(payload: RFQInput, product: Product) -> tuple[Decimal, dict | None]:
    schedule = payload.compute_schedule
    if schedule is None:
        if payload.quantity is None or not payload.requirements:
            raise HTTPException(422, "Quantity and requirements are needed without a Compute schedule")
        validate_quantity(payload.quantity, product.unit)
        return payload.quantity, None
    if product.market != "COMPUTE" or product.unit != "GPU_HOUR":
        raise HTTPException(422, "A Compute schedule requires a GPU-hour product")
    try:
        start = schedule.starts_at.astimezone(UTC)
        end = start + timedelta(hours=schedule.duration_hours)
    except OverflowError:
        raise HTTPException(422, "Compute schedule exceeds the supported date range") from None
    if start <= utcnow():
        raise HTTPException(422, "Compute schedule must start in the future")
    quantity = Decimal(schedule.gpu_count * schedule.duration_hours)
    if payload.quantity is not None and payload.quantity != quantity:
        raise HTTPException(422, "Quantity must match GPU count multiplied by duration hours")
    return quantity, ComputeScheduleView(
        gpu_count=schedule.gpu_count, starts_at=start, duration_hours=schedule.duration_hours, ends_at=end,
    ).model_dump(mode="json")


def require_future_schedule(schedule: dict | None) -> None:
    if schedule is not None and datetime.fromisoformat(schedule["starts_at"]) <= utcnow():
        raise HTTPException(409, "The requested Compute schedule has already started")


def get_rfq(
    db: Session, user: User, rfq_id: str, *, lock: bool = False, organization_id: str | None = None,
) -> RFQ:
    query = visible_to(select(RFQ).where(RFQ.id == rfq_id), RFQ, user, organization_id)
    rfq = db.scalar(query.with_for_update() if lock else query)
    if rfq is None:
        raise HTTPException(404, "RFQ not found")
    return rfq


def get_order(
    db: Session, user: User, order_id: str, *, lock: bool = False, organization_id: str | None = None,
) -> Order:
    query = visible_to(select(Order).where(Order.id == order_id), Order, user, organization_id)
    order = db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    if order is None:
        raise HTTPException(404, "Order not found")
    return order


def require_order_state(order: Order, *states: str) -> None:
    if order.state not in states:
        raise HTTPException(409, f"Action unavailable in order state {order.state}")


def transition(db: Session, user: User, order: Order, to_state: str, action: str, details: dict | None = None) -> None:
    previous = order.state
    order.state = to_state
    audit(db, user, "order", order.id, action, {**(details or {}), "from_state": previous, "to_state": to_state})


def quote_view(quote: Quote) -> QuoteView:
    result = QuoteView.model_validate(quote)
    now = utcnow()
    schedule = quote.snapshot.get("compute_schedule")
    if result.state == "SENT" and (
        quote.expires_at <= now or (schedule and datetime.fromisoformat(schedule["starts_at"]) <= now)
    ):
        result.state = "EXPIRED"
    return result


def transaction_snapshot(product: Product, supplier: Supplier, buyer: Organization, seller: Organization,
                         rfq: RFQ, payload: QuoteInput) -> dict:
    snapshot = {
        "product_id": product.id,
        "product_version": product.version,
        "name": product.name,
        "market": product.market,
        "specification": product.specification,
        "service_region": product.service_region,
        "unit": product.unit,
        "unit_price": str(payload.unit_price),
        "quantity": str(rfq.quantity),
        "currency": product.currency,
        "tax_and_fees": str(payload.tax_and_fees),
        "buyer_organization_id": buyer.id,
        "buyer_organization_name": buyer.name,
        "buyer_registration_country": buyer.registration_country,
        "supplier_organization_id": seller.id,
        "supplier_organization_name": seller.name,
        "supplier_registration_country": seller.registration_country,
        "supplier_id": supplier.id,
        "requirements": rfq.requirements,
        "terms": payload.terms,
    }
    if rfq.compute_schedule is not None:
        snapshot["compute_schedule"] = rfq.compute_schedule
    return snapshot


@router.post("/rfqs", response_model=RFQView, status_code=201)
def submit_rfq(payload: RFQInput, db: DB, user: CurrentUser):
    require_role(db, user, payload.organization_id, *BUYERS)
    product = get_published_product(db, payload.product_id, lock=True)
    quantity, schedule = rfq_quantity_and_schedule(payload, product)
    supplier = db.get(Supplier, product.supplier_id)
    if supplier.organization_id == payload.organization_id:
        raise HTTPException(409, "Buyer and supplier must be different organizations")
    rfq = RFQ(
        buyer_organization_id=payload.organization_id, supplier_organization_id=supplier.organization_id,
        product_id=product.id, quantity=quantity, requirements=payload.requirements or "", compute_schedule=schedule,
    )
    db.add(rfq)
    db.flush()
    audit(db, user, "rfq", rfq.id, "rfq.submitted")
    db.commit()
    return rfq


@router.get("/rfqs", response_model=list[RFQView])
def list_rfqs(db: DB, user: CurrentUser, organization_id: str | None = None, state: RFQState | None = None,
              limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
    query = visible_to(select(RFQ), RFQ, user, organization_id)
    if state is not None:
        query = query.where(RFQ.state == state)
    return db.scalars(query.order_by(RFQ.created_at.desc(), RFQ.id).limit(limit).offset(offset)).all()


@router.get("/rfqs/{rfq_id}", response_model=RFQView)
def read_rfq(rfq_id: str, db: DB, user: CurrentUser, organization_id: str | None = None):
    return get_rfq(db, user, rfq_id, organization_id=organization_id)


@router.post("/rfqs/{rfq_id}/decline", response_model=RFQView)
def decline_rfq(rfq_id: str, payload: RFQDeclineInput, db: DB, user: CurrentUser):
    rfq = get_rfq(db, user, rfq_id, lock=True)
    if not user.is_operator:
        require_role(db, user, rfq.supplier_organization_id, *SELLERS)
    if rfq.state != "SUBMITTED":
        raise HTTPException(409, "Only an unanswered RFQ can be declined")
    rfq.state = "REJECTED"
    audit(db, user, "rfq", rfq.id, "rfq.declined", {
        "reason": payload.reason, "from_state": "SUBMITTED", "to_state": rfq.state,
    })
    db.commit()
    return rfq


@router.post("/rfqs/{rfq_id}/quotes", response_model=QuoteView, status_code=201)
def issue_quote(rfq_id: str, payload: QuoteInput, db: DB, user: CurrentUser):
    rfq = get_rfq(db, user, rfq_id, lock=True)
    if not user.is_operator:
        require_role(db, user, rfq.supplier_organization_id, *SELLERS)
    if rfq.state not in ("SUBMITTED", "QUOTED"):
        raise HTTPException(409, "RFQ cannot be quoted")
    product = get_published_product(db, rfq.product_id, lock=True)
    validate_quantity(rfq.quantity, product.unit)
    require_future_schedule(rfq.compute_schedule)
    if payload.expires_at <= utcnow():
        raise HTTPException(422, "Quote expiration must be in the future")
    supplier = db.get(Supplier, product.supplier_id)
    buyer = db.get(Organization, rfq.buyer_organization_id)
    seller = db.get(Organization, rfq.supplier_organization_id)
    with localcontext() as context:
        context.prec = 34
        subtotal = (payload.unit_price * rfq.quantity).quantize(CENT, rounding=ROUND_HALF_UP)
    amount = subtotal + payload.tax_and_fees
    if not Decimal("0") < amount < Decimal("1e21"):
        raise HTTPException(422, "Quote total is outside supported currency precision")
    quote = Quote(rfq_id=rfq.id, amount=amount, expires_at=payload.expires_at,
                  snapshot=transaction_snapshot(product, supplier, buyer, seller, rfq, payload))
    rfq.state = "QUOTED"
    db.add(quote)
    db.flush()
    audit(db, user, "rfq", rfq.id, "quote.sent", {"quote_id": quote.id})
    db.commit()
    return quote_view(quote)


@router.get("/rfqs/{rfq_id}/quotes", response_model=list[QuoteView])
def list_quotes(rfq_id: str, db: DB, user: CurrentUser,
                limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
    get_rfq(db, user, rfq_id)
    query = select(Quote).where(Quote.rfq_id == rfq_id).order_by(Quote.created_at.desc(), Quote.id)
    quotes = db.scalars(query.limit(limit).offset(offset)).all()
    return [quote_view(quote) for quote in quotes]


@router.get("/quotes/{quote_id}", response_model=QuoteView)
def read_quote(quote_id: str, db: DB, user: CurrentUser):
    quote = db.get(Quote, quote_id)
    if quote is None:
        raise HTTPException(404, "Quote not found")
    get_rfq(db, user, quote.rfq_id)
    return quote_view(quote)


@router.post("/quotes/{quote_id}/accept", response_model=OrderView)
def accept_quote(quote_id: str, db: DB, user: CurrentUser):
    rfq_id = db.scalar(select(Quote.rfq_id).where(Quote.id == quote_id))
    if rfq_id is None:
        raise HTTPException(404, "Quote not found")
    rfq = get_rfq(db, user, rfq_id, lock=True)
    require_role(db, user, rfq.buyer_organization_id, *BUYERS)
    existing = db.scalar(select(Order).where(Order.rfq_id == rfq.id))
    if existing is not None:
        if existing.quote_id == quote_id:
            return existing
        raise HTTPException(409, "A different quote was accepted")
    quote = db.get(Quote, quote_id)
    product = get_published_product(db, rfq.product_id, lock=True)
    if rfq.state != "QUOTED" or quote.state != "SENT" or quote.expires_at <= utcnow():
        raise HTTPException(409, "Quote is no longer available")
    require_future_schedule(quote.snapshot.get("compute_schedule"))
    if product.version != quote.snapshot["product_version"]:
        raise HTTPException(409, "Product changed after the quote was issued")
    snapshot = {**quote.snapshot, "quote_id": quote.id, "buyer_accepted_by": user.id, "amount": str(quote.amount)}
    order = Order(buyer_organization_id=rfq.buyer_organization_id, supplier_organization_id=rfq.supplier_organization_id,
                  supplier_id=product.supplier_id, rfq_id=rfq.id, quote_id=quote.id, market=product.market,
                  snapshot=snapshot, currency=snapshot["currency"], amount=quote.amount)
    db.add(order)
    rfq.state = "ACCEPTED"
    quote.state = "ACCEPTED"
    db.execute(update(Quote).where(Quote.rfq_id == rfq.id, Quote.id != quote.id, Quote.state == "SENT")
               .values(state="DECLINED"))
    db.flush()
    audit(db, user, "rfq", rfq.id, "quote.accepted", {"quote_id": quote.id, "order_id": order.id})
    audit(db, user, "order", order.id, "order.created", {"quote_id": quote.id, "to_state": order.state})
    db.commit()
    return order


def order_refunds(db: Session, order_id: str) -> list[Refund]:
    return list(db.scalars(select(Refund).where(Refund.order_id == order_id).order_by(Refund.created_at, Refund.id)))


def require_no_approved_refund(db: Session, order: Order) -> None:
    if db.scalar(select(Refund.id).where(Refund.order_id == order.id, Refund.state == "APPROVED")) is not None:
        raise HTTPException(409, "An approved refund is awaiting external confirmation")


def require_token_calls_settled(db: Session, order: Order) -> None:
    if order.market != "TOKEN_API":
        return
    from .token import Usage

    if db.scalar(select(Usage.id).where(
        Usage.order_id == order.id, Usage.status.in_(("PENDING", "REVIEW_REQUIRED"))
    ).limit(1)) is not None:
        raise HTTPException(409, "Reconcile unresolved Token calls before refunding the order")


def statement(order: Order, payment: Payment | None, refunds: list[Refund]) -> StatementView:
    refunded = sum((refund.amount for refund in refunds if refund.state == "PROCESSED"), ZERO)
    if payment is None:
        status = "UNPAID"
    elif refunded == payment.amount:
        status = "REFUNDED"
    elif any(refund.state in ("REQUESTED", "APPROVED") for refund in refunds):
        status = "REFUND_REQUESTED"
    elif refunded:
        status = "PARTIALLY_REFUNDED"
    else:
        status = "PAID"
    return StatementView(order_id=order.id, order_state=order.state, currency=order.currency, amount=order.amount,
                         verified_paid=payment.amount if payment else ZERO, verified_refunded=refunded,
                         payment_status=status)


@router.get("/orders", response_model=list[OrderView])
def list_orders(db: DB, user: CurrentUser, organization_id: str | None = None, state: ShortText | None = None,
                limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
    query = visible_to(select(Order), Order, user, organization_id)
    if state is not None:
        query = query.where(Order.state == state)
    return db.scalars(query.order_by(Order.created_at.desc(), Order.id).limit(limit).offset(offset)).all()


def compute_reservation_summary(db: Session, order: Order) -> ComputeReservationSummary | None:
    if "compute_schedule" not in order.snapshot:
        return None
    from .fulfillment import reservation_view

    reservation = reservation_view(db, order)
    return ComputeReservationSummary.model_validate(reservation) if reservation is not None else None


def visible_contract(
    db: Session, order: Order, contract: Contract | None, user: User,
    reservation: ComputeReservationSummary | None,
) -> ContractView | None:
    if contract is None:
        return None
    view = ContractView.model_validate(contract)
    if user.is_operator or "compute_schedule" not in order.snapshot:
        return view
    if (order.state != "CANCELLED" and contract.buyer_confirmed_at
            and contract.supplier_confirmed_at and reservation is not None):
        if reservation.status in ("CONFIRMED", "PROVISIONED"):
            return view
        if reservation.status == "HELD":
            from .fulfillment import compute_reservation_allows_payment

            if compute_reservation_allows_payment(db, order):
                return view
    view.payment_instructions = None
    return view


@router.get("/orders/{order_id}", response_model=OrderDetail)
def read_order(order_id: str, db: DB, user: CurrentUser, organization_id: str | None = None):
    from .attachments import REF_PREFIX, may_read_private_order_docs

    order = get_order(db, user, order_id, organization_id=organization_id)
    contract = db.get(Contract, order.id)
    reservation = compute_reservation_summary(db, order)
    payment = db.get(Payment, order.id)
    refunds = order_refunds(db, order.id)
    disputes = db.scalars(select(Dispute).where(Dispute.order_id == order.id)
                          .order_by(Dispute.created_at, Dispute.id)).all()
    visible_payment = payment
    if payment and payment.bank_credited_at is not None and not user.is_operator:
        visible_payment = PaymentView.model_validate(payment).model_copy(update={"evidence_ref": None})
    visible_refunds = refunds
    contract_summary = visible_contract(db, order, contract, user, reservation)
    if not may_read_private_order_docs(db, order, user, organization_id):
        redacted_references = {"external_reference": None, "evidence_ref": None}
        if organization_id is not None and contract_summary is not None:
            contract_summary.payment_instructions = None
        if payment and (organization_id is not None or payment.evidence_ref.startswith(REF_PREFIX)):
            visible_payment = PaymentView.model_validate(payment).model_copy(update=redacted_references)
        visible_refunds = [
            RefundView.model_validate(refund).model_copy(update=redacted_references)
            if organization_id is not None or (refund.evidence_ref or "").startswith(REF_PREFIX) else refund
            for refund in refunds
        ]
    return {
        **OrderView.model_validate(order).model_dump(),
        "contract": contract_summary,
        "payment": visible_payment,
        "statement": statement(order, payment, refunds),
        "disputes": disputes,
        "refunds": visible_refunds,
        "compute_reservation": reservation,
    }


@router.get("/orders/{order_id}/timeline", response_model=list[TimelineEvent])
def order_timeline(order_id: str, db: DB, user: CurrentUser,
                   limit: Annotated[int, Query(ge=1, le=100)] = 50,
                   after_id: Annotated[int, Query(ge=0)] = 0):
    get_order(db, user, order_id)
    return db.scalars(select(AuditLog).where(AuditLog.entity_type == "order", AuditLog.entity_id == order_id,
                                            AuditLog.id > after_id).order_by(AuditLog.id).limit(limit)).all()


@router.get(
    "/orders/{order_id}/statement.csv", response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def export_order_statement(order_id: str, db: DB, user: CurrentUser) -> Response:
    # Keep the export consistent with concurrent payment and refund transitions.
    order = get_order(db, user, order_id, lock=True)
    record = statement(order, db.get(Payment, order.id), order_refunds(db, order.id))
    output = StringIO(newline="")
    rows = writer(output)
    rows.writerow(("order_id", "order_state", "currency", "amount", "verified_paid", "verified_refunded", "payment_status"))
    rows.writerow((record.order_id, record.order_state, record.currency, format(record.amount, "f"),
                   format(record.verified_paid, "f"), format(record.verified_refunded, "f"), record.payment_status))
    return Response(
        content=output.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="aix-statement-{order.id}.csv"'},
    )


@router.post("/orders/{order_id}/contract", response_model=ContractView)
def archive_contract(order_id: str, payload: ContractInput, db: DB, user: Operator):
    from .attachments import require_order_attachment
    from .fulfillment import ensure_compute_reservation, require_verified_compute_resource

    order = get_order(db, user, order_id, lock=True)
    contract = db.get(Contract, order.id)
    if contract is not None:
        if ContractView.model_validate(contract).model_dump(exclude={"buyer_confirmed_at", "supplier_confirmed_at"}) == payload.model_dump():
            return contract
        raise HTTPException(409, "Contract version is already fixed")
    require_order_state(order, "CONTRACT_PENDING")
    attachment = require_order_attachment(db, order, payload.evidence_ref, "CONTRACT")
    if attachment is not None and attachment.sha256 != payload.sha256:
        raise HTTPException(422, "Contract SHA-256 must match the uploaded attachment")
    if "compute_schedule" in order.snapshot:
        ensure_compute_reservation(db, order, user)
    elif order.market == "COMPUTE":
        require_verified_compute_resource(db, order)
    contract = Contract(order_id=order.id, **payload.model_dump())
    db.add(contract)
    audit(db, user, "order", order.id, "contract.archived", {"version": contract.version, "sha256": contract.sha256})
    db.commit()
    return contract


@router.post("/orders/{order_id}/contract/confirm", response_model=ContractView)
def confirm_contract(order_id: str, payload: ContractConfirmation, db: DB, user: CurrentUser):
    order = get_order(db, user, order_id, lock=True)
    if payload.organization_id == order.buyer_organization_id:
        require_role(db, user, payload.organization_id, *BUYERS)
        field = "buyer_confirmed_at"
    elif payload.organization_id == order.supplier_organization_id:
        require_role(db, user, payload.organization_id, *SELLERS)
        field = "supplier_confirmed_at"
    else:
        raise HTTPException(404, "Organization is not a party to the contract")
    contract = db.get(Contract, order.id)
    if contract is None:
        raise HTTPException(409, "Archive a contract version before confirming it")
    if getattr(contract, field) is not None:
        return visible_contract(db, order, contract, user, compute_reservation_summary(db, order))
    require_order_state(order, "CONTRACT_PENDING")
    setattr(contract, field, utcnow())
    audit(db, user, "order", order.id, "contract.confirmed", {"organization_id": payload.organization_id,
                                                               "version": contract.version})
    if contract.buyer_confirmed_at and contract.supplier_confirmed_at:
        transition(db, user, order, "PAYMENT_PENDING", "payment.awaiting_verification")
    db.commit()
    return visible_contract(db, order, contract, user, compute_reservation_summary(db, order))


def normalized_payment(payload: PaymentInput) -> PaymentInput:
    channel = payload.channel.lower()
    if len(channel) > 160:
        raise HTTPException(422, "Normalized payment channel is too long")
    return payload.model_copy(update={"channel": channel})


def require_pending_payment(order: Order, payload: PaymentInput) -> None:
    require_order_state(order, "PAYMENT_PENDING")
    if payload.amount != order.amount or payload.currency != order.currency:
        raise HTTPException(422, "Payment must match the order amount and currency")


def pending_payment_receipt(db: Session, order: Order) -> PaymentReceipt | None:
    return db.scalar(select(PaymentReceipt).where(PaymentReceipt.order_id == order.id, PaymentReceipt.state == "PENDING"))


def record_verified_payment(
    db: Session, user: User, order: Order, payload: PaymentInput, *, bank_credited_at: datetime,
) -> None:
    require_pending_payment(order, payload)
    if "compute_schedule" in order.snapshot:
        from .fulfillment import confirm_compute_reservation

        confirm_compute_reservation(db, order, user)
    payment = Payment(order_id=order.id, verified_by=user.id, bank_credited_at=bank_credited_at, **payload.model_dump())
    db.add(payment)
    transition(db, user, order, "PAID", "payment.verified",
               {"channel": payload.channel, "verification": "BANK_CREDIT"})
    try:
        db.flush()
    except IntegrityError as error:
        if getattr(error.orig, "sqlstate", None) != "23505":
            raise
        db.rollback()
        raise HTTPException(409, "Payment reference already linked to another order") from None


@router.post("/orders/{order_id}/payment-receipts", response_model=PaymentReceiptView, status_code=201)
def submit_payment_receipt(order_id: UUID, payload: PaymentInput, db: DB, user: CurrentUser):
    from .attachments import require_order_attachment

    order = get_order(db, user, str(order_id), lock=True)
    require_role(db, user, order.buyer_organization_id, *BUYERS, "FINANCE")
    payload = normalized_payment(payload)
    require_pending_payment(order, payload)
    attachment = require_order_attachment(db, order, payload.evidence_ref, "PAYMENT")
    if attachment is None:
        raise HTTPException(422, "Upload a payment attachment before submitting a receipt")
    payload = payload.model_copy(update={"evidence_ref": attachment.reference})
    receipt = pending_payment_receipt(db, order)
    if receipt is not None:
        if PaymentInput.model_validate(receipt, from_attributes=True) == payload:
            return receipt
        raise HTTPException(409, "Order already has a pending payment receipt")
    receipt = PaymentReceipt(
        order_id=order.id, attachment_id=attachment.id, submitted_by=user.id,
        **payload.model_dump(exclude={"evidence_ref"}),
    )
    db.add(receipt)
    db.flush()
    audit(db, user, "payment_receipt", receipt.id, "payment_receipt.submitted", {"order_id": order.id})
    db.commit()
    return receipt


@router.get("/orders/{order_id}/payment-receipts", response_model=list[PaymentReceiptView])
def list_payment_receipts(
    order_id: UUID, db: DB, user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0,
):
    from .attachments import may_read_private_order_docs

    order = get_order(db, user, str(order_id))
    if not may_read_private_order_docs(db, order, user):
        raise HTTPException(404, "Payment receipts not found")
    rows = select(PaymentReceipt).where(PaymentReceipt.order_id == order.id)
    return db.scalars(rows.order_by(PaymentReceipt.submitted_at.desc(), PaymentReceipt.id).limit(limit).offset(offset)).all()


@router.post("/orders/{order_id}/payment-receipts/{receipt_id}/review", response_model=PaymentReceiptView)
def review_payment_receipt(
    order_id: UUID, receipt_id: UUID, payload: PaymentReceiptReview, db: DB, user: Operator,
):
    from .attachments import require_order_attachment

    order = get_order(db, user, str(order_id), lock=True)
    receipt = db.get(PaymentReceipt, str(receipt_id))
    if receipt is None or receipt.order_id != order.id:
        raise HTTPException(404, "Payment receipt not found")
    state = "APPROVED" if payload.decision == "APPROVE" else "REJECTED"
    credit = payload.bank_credit
    if state == "REJECTED" and credit is not None:
        raise HTTPException(422, "Bank credit details are only accepted for approval")
    claimed = PaymentInput.model_validate(receipt, from_attributes=True)
    actual = normalized_payment(PaymentInput(**credit.model_dump(exclude={"credited_at"}))) if credit else claimed
    credited_at = credit.credited_at if credit else None
    if receipt.state != "PENDING":
        if receipt.state == state and receipt.review_note == payload.note:
            if state == "REJECTED":
                return receipt
            payment = db.get(Payment, order.id)
            if (payment and payment.bank_credited_at == credited_at
                    and PaymentInput.model_validate(payment, from_attributes=True) == actual):
                return receipt
        raise HTTPException(409, "Payment receipt was already reviewed")
    require_order_state(order, "PAYMENT_PENDING")
    if state == "APPROVED":
        if credit is None:
            raise HTTPException(422, "Receiving account credit details and proof are required")
        if actual.model_dump(exclude={"evidence_ref"}) != claimed.model_dump(exclude={"evidence_ref"}):
            raise HTTPException(422, "Receiving account credit must match the receipt channel, reference, amount and currency")
        if not order.created_at <= credit.credited_at <= utcnow():
            raise HTTPException(422, "Receiving account credit time must be between order creation and now")
        bank_file = require_order_attachment(db, order, actual.evidence_ref, "BANK_CREDIT")
        buyer_file = require_order_attachment(db, order, claimed.evidence_ref, "PAYMENT")
        if bank_file is None or buyer_file is None or bank_file.sha256 == buyer_file.sha256:
            raise HTTPException(422, "Attach independent proof from the receiving account")
        record_verified_payment(db, user, order, actual, bank_credited_at=credit.credited_at)
    receipt.state = state
    receipt.review_note = payload.note
    receipt.reviewed_by = user.id
    receipt.reviewed_at = utcnow()
    audit(db, user, "payment_receipt", receipt.id, "payment_receipt.reviewed", {"order_id": order.id, "state": state})
    db.commit()
    return receipt


@router.post("/orders/{order_id}/start-fulfillment", response_model=OrderView)
def start_fulfillment(order_id: str, db: DB, user: Operator):
    order = get_order(db, user, order_id, lock=True)
    if order.state == "FULFILLING":
        return order
    require_order_state(order, "PAID")
    require_no_approved_refund(db, order)
    transition(db, user, order, "FULFILLING", "fulfillment.started")
    db.commit()
    return order


@router.post("/orders/{order_id}/delivery-evidences", response_model=OrderView)
def deliver_order(order_id: str, payload: EvidenceInput, db: DB, user: CurrentUser):
    from .attachments import require_order_attachment
    from .fulfillment import require_fulfillment_evidence

    order = get_order(db, user, order_id, lock=True)
    if not user.is_operator:
        require_role(db, user, order.supplier_organization_id, *SELLERS)
    require_order_state(order, "FULFILLING")
    require_no_approved_refund(db, order)
    require_order_attachment(db, order, payload.evidence_ref, "DELIVERY")
    require_fulfillment_evidence(db, order)
    transition(db, user, order, "DELIVERED", "order.delivered", payload.model_dump())
    db.commit()
    return order


@router.post("/orders/{order_id}/acceptance", response_model=OrderView)
def accept_delivery(order_id: str, payload: AcceptanceInput, db: DB, user: CurrentUser):
    order = get_order(db, user, order_id, lock=True)
    require_role(db, user, order.buyer_organization_id, *BUYERS)
    if order.state in ("ACCEPTED", "COMPLETED"):
        return order
    require_order_state(order, "DELIVERED")
    transition(db, user, order, "ACCEPTED", "delivery.accepted", payload.model_dump(exclude_none=True))
    db.commit()
    return order


@router.post("/orders/{order_id}/complete", response_model=OrderView)
def complete_order(order_id: str, db: DB, user: Operator):
    order = get_order(db, user, order_id, lock=True)
    if order.state == "COMPLETED":
        return order
    require_order_state(order, "ACCEPTED")
    if any(refund.state in ("REQUESTED", "APPROVED") for refund in order_refunds(db, order.id)):
        raise HTTPException(409, "Resolve the pending refund before completing the order")
    transition(db, user, order, "COMPLETED", "order.completed")
    db.commit()
    return order


@router.post("/orders/{order_id}/disputes", response_model=DisputeView, status_code=201)
def open_dispute(order_id: str, payload: DisputeInput, db: DB, user: CurrentUser):
    order = get_order(db, user, order_id, lock=True)
    require_role(db, user, order.buyer_organization_id, *BUYERS)
    require_order_state(order, "PAID", "FULFILLING", "DELIVERED", "ACCEPTED")
    dispute = Dispute(order_id=order.id, previous_order_state=order.state, reason=payload.reason)
    db.add(dispute)
    db.flush()
    transition(db, user, order, "DISPUTED", "dispute.opened", {"dispute_id": dispute.id})
    db.commit()
    return dispute


def get_dispute(db: Session, order: Order, dispute_id: str) -> Dispute:
    dispute = db.get(Dispute, dispute_id)
    if dispute is None or dispute.order_id != order.id:
        raise HTTPException(404, "Dispute not found")
    if dispute.resolved_at is not None:
        raise HTTPException(409, "Dispute already resolved")
    return dispute


@router.post("/orders/{order_id}/disputes/{dispute_id}/evidences", status_code=204)
def add_dispute_evidence(order_id: str, dispute_id: str, payload: EvidenceInput, db: DB, user: CurrentUser):
    from .attachments import require_order_attachment

    order = get_order(db, user, order_id, lock=True)
    get_dispute(db, order, dispute_id)
    require_order_attachment(db, order, payload.evidence_ref, "DISPUTE")
    audit(db, user, "order", order.id, "dispute.evidence_added", {**payload.model_dump(), "dispute_id": dispute_id})
    db.commit()


@router.post("/orders/{order_id}/disputes/{dispute_id}/resolve", response_model=DisputeView)
def resolve_dispute(order_id: str, dispute_id: str, payload: DisputeResolution, db: DB, user: Operator):
    order = get_order(db, user, order_id, lock=True)
    dispute = get_dispute(db, order, dispute_id)
    require_order_state(order, "DISPUTED", "CANCELLED")
    if payload.redeliver and order.market == "TOKEN_API" and any(
        refund.state in ("APPROVED", "PROCESSED") for refund in order_refunds(db, order.id)
    ):
        raise HTTPException(409, "The original Token entitlement cannot be reopened after refund approval")
    dispute.decision = payload.decision
    dispute.resolved_at = utcnow()
    state = order.state if order.state == "CANCELLED" else (
        "FULFILLING" if payload.redeliver else dispute.previous_order_state
    )
    transition(db, user, order, state, "dispute.resolved", {"dispute_id": dispute_id})
    db.commit()
    return dispute


@router.post("/orders/{order_id}/refund-requests", response_model=RefundView, status_code=201)
def request_refund(order_id: str, payload: RefundInput, db: DB, user: CurrentUser):
    order = get_order(db, user, order_id, lock=True)
    require_role(db, user, order.buyer_organization_id, *BUYERS, "FINANCE")
    payment = db.get(Payment, order.id)
    if payment is None:
        raise HTTPException(409, "Refund requires a verified payment")
    refunds = order_refunds(db, order.id)
    if any(refund.state in ("REQUESTED", "APPROVED") for refund in refunds):
        raise HTTPException(409, "Order already has a pending refund request")
    refunded = statement(order, payment, refunds).verified_refunded
    if payload.amount > payment.amount - refunded:
        raise HTTPException(422, "Refund exceeds the remaining paid amount")
    refund = Refund(order_id=order.id, **payload.model_dump())
    db.add(refund)
    db.flush()
    audit(db, user, "order", order.id, "refund.requested", {"refund_id": refund.id, "amount": str(refund.amount)})
    db.commit()
    return refund


def get_refund(db: Session, order: Order, refund_id: str) -> Refund:
    refund = db.get(Refund, refund_id)
    if refund is None or refund.order_id != order.id:
        raise HTTPException(404, "Refund not found")
    return refund


@router.post("/orders/{order_id}/refund-requests/{refund_id}/review", response_model=RefundView)
def review_refund(order_id: str, refund_id: str, payload: RefundReview, db: DB, user: Operator):
    order = get_order(db, user, order_id, lock=True)
    refund = get_refund(db, order, refund_id)
    if payload.decision == "APPROVE":
        if refund.state != "REQUESTED":
            raise HTTPException(409, "Only a requested refund can be approved")
        require_token_calls_settled(db, order)
        if order.market == "TOKEN_API" and order.state not in ("DELIVERED", "ACCEPTED", "COMPLETED"):
            payment = db.get(Payment, order.id)
            refunded = statement(order, payment, order_refunds(db, order.id)).verified_refunded
            if refund.amount + refunded < payment.amount:
                raise HTTPException(409, "A partial Token refund requires completed delivery")
        refund.state = "APPROVED"
    else:
        if refund.state not in ("REQUESTED", "APPROVED"):
            raise HTTPException(409, "Only a pending refund can be rejected")
        refund.state = "REJECTED"
    audit(db, user, "order", order.id, "refund.reviewed", {"refund_id": refund_id, "state": refund.state,
                                                          "note": payload.note})
    db.commit()
    return refund


@router.post("/orders/{order_id}/refund-requests/{refund_id}/confirm", response_model=RefundView)
def verify_refund(order_id: str, refund_id: str, payload: RefundConfirmation, db: DB, user: Operator):
    from .attachments import require_order_attachment

    order = get_order(db, user, order_id, lock=True)
    refund = get_refund(db, order, refund_id)
    if (refund.state == "PROCESSED" and refund.external_reference == payload.external_reference
            and refund.evidence_ref == payload.evidence_ref):
        return refund
    if refund.state != "APPROVED":
        raise HTTPException(409, "Only an approved refund can be confirmed")
    require_order_attachment(db, order, payload.evidence_ref, "REFUND")
    require_token_calls_settled(db, order)
    payment = db.get(Payment, order.id)
    refund.state = "PROCESSED"
    refund.external_reference = payload.external_reference
    refund.evidence_ref = payload.evidence_ref
    try:
        refunded = statement(order, payment, order_refunds(db, order.id)).verified_refunded
        details = {"refund_id": refund.id, "amount": str(refund.amount)}
        if refunded == payment.amount:
            if "compute_schedule" in order.snapshot:
                from .fulfillment import release_compute_reservation

                release_compute_reservation(db, order, user)
            transition(db, user, order, "CANCELLED", "refund.verified", details)
        else:
            audit(db, user, "order", order.id, "refund.verified", details)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Refund reference already linked to another refund") from None
    return refund


@router.get("/organizations/{organization_id}/supplier-overview")
def supplier_overview(organization_id: str, db: DB, user: CurrentUser) -> dict[str, int]:
    require_role(db, user, organization_id, *SELLERS)
    orders = select(func.count(Order.id)).where(
        Order.supplier_organization_id == organization_id, Order.state.not_in(("COMPLETED", "CANCELLED")),
    ).scalar_subquery()
    quotes = select(func.count(RFQ.id)).where(
        RFQ.supplier_organization_id == organization_id, RFQ.state == "SUBMITTED",
    ).scalar_subquery()
    active_orders, pending_quotes = db.execute(select(orders, quotes)).one()
    return {"active_orders": active_orders, "pending_quotes": pending_quotes}


@router.get("/billing/statements", response_model=list[StatementView])
def billing_statements(db: DB, user: CurrentUser, organization_id: str | None = None,
                       limit: Annotated[int, Query(ge=1, le=100)] = 50,
                       offset: Annotated[int, Query(ge=0)] = 0):
    query = visible_to(select(Order, Payment).outerjoin(Payment, Payment.order_id == Order.id),
                       Order, user, organization_id)
    rows = db.execute(query.order_by(Order.created_at.desc(), Order.id).limit(limit).offset(offset)).all()
    refunds_by_order = {order.id: [] for order, _ in rows}
    if refunds_by_order:
        refunds = db.scalars(select(Refund).where(Refund.order_id.in_(refunds_by_order)))
        for refund in refunds:
            refunds_by_order[refund.order_id].append(refund)
    return [statement(order, payment, refunds_by_order[order.id]) for order, payment in rows]
