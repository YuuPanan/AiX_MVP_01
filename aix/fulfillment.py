from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, and_, or_, select, union_all
from sqlalchemy.orm import Mapped, Session, mapped_column

from .audit import audit
from .catalog import Product, SupplierEvidence, check_supplier_access, get_published_product, get_supplier
from .db import Base, DB, utcnow
from .identity import CurrentUser, Operator, User
from .procurement import Contract, Order, get_order, require_order_state

router = APIRouter(tags=["compute"])
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class Resource(Base):
    __tablename__ = "compute_resources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    product_version: Mapped[int]
    physical_region: Mapped[str] = mapped_column(String(200))
    gpu_model: Mapped[str] = mapped_column(String(200))
    gpu_count: Mapped[int]
    vram_gb: Mapped[int]
    ownership_reference: Mapped[str] = mapped_column(String(1000))
    rights_valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    benchmark_reference: Mapped[str] = mapped_column(String(1000))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class Provision(Base):
    __tablename__ = "compute_provisions"
    __table_args__ = (Index("ix_compute_provision_resource_period", "resource_id", "starts_at", "ends_at"),)

    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    resource_id: Mapped[str] = mapped_column(ForeignKey("compute_resources.id"))
    gpu_count: Mapped[int]
    instance_reference: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_owner: Mapped[str | None] = mapped_column(String(200))
    opening_attachment_id: Mapped[str | None] = mapped_column(ForeignKey("attachments.id"))


class Reservation(Base):
    __tablename__ = "compute_reservations"
    __table_args__ = (
        CheckConstraint("gpu_count > 0", name="ck_compute_reservation_gpu_count"),
        CheckConstraint("starts_at < ends_at", name="ck_compute_reservation_period"),
        CheckConstraint("expires_at <= starts_at", name="ck_compute_reservation_expiry"),
        CheckConstraint(
            "status IN ('HELD', 'CONFIRMED', 'RELEASED', 'PROVISIONED')",
            name="ck_compute_reservation_status",
        ),
        Index("ix_compute_reservation_resource_period", "resource_id", "starts_at", "ends_at"),
    )

    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    resource_id: Mapped[str] = mapped_column(ForeignKey("compute_resources.id"))
    gpu_count: Mapped[int]
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12))


class SLAEvent(Base):
    __tablename__ = "compute_sla_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("compute_provisions.order_id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidence_ref: Mapped[str] = mapped_column(String(1000))
    notes: Mapped[str] = mapped_column(String(2000))


class ResourceInput(BaseModel):
    product_id: UUID
    physical_region: Label
    gpu_model: Label
    gpu_count: Annotated[int, Field(ge=1, le=10_000)]
    vram_gb: Annotated[int, Field(ge=1, le=1024)]
    ownership_reference: Reference
    rights_valid_until: AwareDatetime
    benchmark_reference: Reference


class ResourceView(ResourceInput):
    id: str
    product_version: int
    verified_at: AwareDatetime | None
    verified_by: str | None


class VerificationInput(BaseModel):
    note: Note


class ProvisionInput(BaseModel):
    resource_id: UUID
    instance_reference: Label
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    opened_at: AwareDatetime | None = None
    delivery_owner: Label | None = None
    opening_attachment_id: UUID | None = None

    @model_validator(mode="after")
    def require_complete_opening_evidence(self):
        fields = (self.opened_at, self.delivery_owner, self.opening_attachment_id)
        if any(value is None for value in fields) and any(value is not None for value in fields):
            raise ValueError("Opening time, delivery owner and opening attachment are required together")
        return self


class ProvisionView(ProvisionInput):
    order_id: str
    gpu_count: int
    recorded_at: AwareDatetime


class ReservationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    resource_id: str
    gpu_count: int
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    expires_at: AwareDatetime
    status: Literal["HELD", "CONFIRMED", "EXPIRED", "RELEASED", "PROVISIONED"]


class SLAInput(BaseModel):
    kind: Literal["OUTAGE", "RECOVERY", "SLA_BREACH"]
    occurred_at: AwareDatetime
    evidence_ref: Reference
    notes: Note


class SLAView(SLAInput):
    id: str
    order_id: str


def require_verified_compute_resource(db: Session, order: Order) -> None:
    product_id = order.snapshot["product_id"]
    get_published_product(db, product_id, lock=True)
    verified = db.scalar(select(Resource.id).where(
        Resource.product_id == product_id,
        Resource.verified_at.is_not(None),
        Resource.rights_valid_until > utcnow(),
    ).limit(1))
    if verified is None:
        raise HTTPException(409, "Verify resource rights and benchmark before issuing the Compute contract")


def reservation_status(reservation: Reservation, now: datetime) -> str:
    if reservation.status == "HELD" and reservation.expires_at <= now:
        return "EXPIRED"
    return reservation.status


def reservation_view(db: Session, order: Order) -> ReservationView | None:
    reservation = db.get(Reservation, order.id)
    if reservation is None:
        return None
    return ReservationView.model_validate(reservation).model_copy(
        update={"status": reservation_status(reservation, utcnow())},
    )


def capacity_available(db: Session, resource: Resource, starts_at: datetime, ends_at: datetime,
                       gpu_count: int, now: datetime) -> bool:
    # Both writers hold the resource row lock. Provisioned reservations are counted by the actual provision.
    overlapping = db.execute(union_all(
        select(Provision.starts_at, Provision.ends_at, Provision.gpu_count).where(
            Provision.resource_id == resource.id, Provision.starts_at < ends_at, Provision.ends_at > starts_at,
        ),
        select(Reservation.starts_at, Reservation.ends_at, Reservation.gpu_count).where(
            Reservation.resource_id == resource.id, Reservation.starts_at < ends_at, Reservation.ends_at > starts_at,
            or_(Reservation.status == "CONFIRMED", and_(Reservation.status == "HELD", Reservation.expires_at > now)),
        ),
    )).all()
    changes = [(starts_at, gpu_count), (ends_at, -gpu_count)]
    for start, end, count in overlapping:
        changes.extend(((max(start, starts_at), count), (min(end, ends_at), -count)))
    reserved = 0
    for _, change in sorted(changes):
        reserved += change
        if reserved > resource.gpu_count:
            return False
    return True


def require_schedule_rights(db: Session, order: Order, ends_at: datetime, *, lock: bool = True) -> Product:
    try:
        product = get_published_product(db, order.snapshot["product_id"], lock=lock)
    except HTTPException as error:
        if error.status_code == 404:
            raise HTTPException(409, "Scheduled product is no longer available") from None
        raise
    evidence = db.get(SupplierEvidence, product.publication_evidence_id)
    if evidence is None or evidence.valid_until is None or evidence.valid_until < ends_at:
        raise HTTPException(409, "Supplier authorization must cover the entire scheduled window")
    return product


def verified_resource_covers(resource: Resource | None, ends_at: datetime) -> bool:
    return resource is not None and resource.verified_at is not None and resource.rights_valid_until >= ends_at


def compute_reservation_allows_payment(db: Session, order: Order) -> bool:
    reservation = db.get(Reservation, order.id)
    if reservation is None or reservation_status(reservation, utcnow()) != "HELD":
        return False
    try:
        require_schedule_rights(db, order, reservation.ends_at, lock=False)
    except HTTPException as error:
        if error.status_code in (404, 409):
            return False
        raise
    resource = db.get(Resource, reservation.resource_id)
    return (
        verified_resource_covers(resource, reservation.ends_at)
        and reservation_status(reservation, utcnow()) == "HELD"
    )


def ensure_compute_reservation(db: Session, order: Order, user: User) -> None:
    schedule = order.snapshot.get("compute_schedule")
    if schedule is None:
        return
    reservation = db.get(Reservation, order.id)
    if reservation is not None and reservation.status != "HELD":
        raise HTTPException(409, "Reservation cannot be renewed in its current state")

    starts_at = datetime.fromisoformat(schedule["starts_at"])
    ends_at = datetime.fromisoformat(schedule["ends_at"])
    gpu_count = schedule["gpu_count"]
    if starts_at <= utcnow():
        raise HTTPException(409, "Scheduled start has passed")
    product = require_schedule_rights(db, order, ends_at)
    if reservation is not None and reservation_status(reservation, utcnow()) == "HELD":
        resource = db.get(Resource, reservation.resource_id, with_for_update=True)
        if reservation_status(reservation, utcnow()) == "HELD":
            if not verified_resource_covers(resource, ends_at):
                raise HTTPException(409, "Verified resource must cover the entire scheduled window")
            return
    resources = db.scalars(select(Resource).where(
        Resource.product_id == product.id,
        Resource.verified_at.is_not(None),
        Resource.rights_valid_until >= ends_at,
        Resource.gpu_count >= gpu_count,
    ).order_by(Resource.id).with_for_update()).all()
    now = utcnow()
    if starts_at <= now:
        raise HTTPException(409, "Scheduled start has passed")
    for resource in resources:
        if not capacity_available(db, resource, starts_at, ends_at, gpu_count, now):
            continue
        if reservation is None:
            reservation = Reservation(order_id=order.id)
            db.add(reservation)
        reservation.resource_id = resource.id
        reservation.gpu_count = gpu_count
        reservation.starts_at = starts_at
        reservation.ends_at = ends_at
        reservation.expires_at = min(now + timedelta(hours=24), starts_at)
        reservation.status = "HELD"
        audit(db, user, "order", order.id, "COMPUTE_RESERVATION_HELD", {
            "resource_id": resource.id, "expires_at": reservation.expires_at.isoformat(),
        })
        return
    raise HTTPException(409, "No verified capacity for the scheduled window")


def confirm_compute_reservation(db: Session, order: Order, user: User) -> None:
    if order.snapshot.get("compute_schedule") is None:
        return
    reservation = db.get(Reservation, order.id)
    if reservation is None or reservation_status(reservation, utcnow()) != "HELD":
        raise HTTPException(409, "An active Compute reservation is required for payment confirmation")
    require_schedule_rights(db, order, reservation.ends_at)
    resource = db.get(Resource, reservation.resource_id, with_for_update=True)
    if reservation_status(reservation, utcnow()) != "HELD":
        raise HTTPException(409, "Compute reservation expired during payment confirmation")
    if not verified_resource_covers(resource, reservation.ends_at):
        raise HTTPException(409, "Verified resource must cover the entire scheduled window")
    reservation.status = "CONFIRMED"
    audit(db, user, "order", order.id, "COMPUTE_RESERVATION_CONFIRMED", {"resource_id": reservation.resource_id})


def release_compute_reservation(db: Session, order: Order, user: User) -> None:
    reservation = db.get(Reservation, order.id)
    if reservation is None or reservation.status not in ("HELD", "CONFIRMED"):
        return
    db.get(Product, order.snapshot["product_id"], with_for_update=True)
    db.get(Resource, reservation.resource_id, with_for_update=True)
    reservation.status = "RELEASED"
    audit(db, user, "order", order.id, "COMPUTE_RESERVATION_RELEASED", {"resource_id": reservation.resource_id})


@router.get("/compute/orders/{order_id}/reservation", response_model=ReservationView)
def get_reservation(order_id: UUID, db: DB, user: CurrentUser):
    order = get_order(db, user, str(order_id))
    reservation = reservation_view(db, order)
    if reservation is None:
        raise HTTPException(404, "Compute reservation not found")
    return reservation


@router.post("/compute/orders/{order_id}/reservation", response_model=ReservationView)
def refresh_reservation(order_id: UUID, db: DB, user: Operator):
    order = get_order(db, user, str(order_id), lock=True)
    require_order_state(order, "CONTRACT_PENDING", "PAYMENT_PENDING")
    if order.snapshot.get("compute_schedule") is None:
        raise HTTPException(409, "Order has no Compute schedule")
    if db.get(Contract, order.id) is None:
        raise HTTPException(409, "Archive a contract before renewing a reservation")
    ensure_compute_reservation(db, order, user)
    db.flush()
    reservation = reservation_view(db, order)
    db.commit()
    return reservation


@router.post("/compute/resources", response_model=ResourceView, status_code=201)
def register_resource(body: ResourceInput, db: DB, user: CurrentUser):
    from .attachments import require_supplier_attachment

    product = db.get(Product, str(body.product_id))
    if product is None or product.market != "COMPUTE":
        raise HTTPException(404, "Compute product not found")
    supplier = get_supplier(db, product.supplier_id)
    check_supplier_access(db, user, supplier)
    spec = product.specification
    if (
        body.physical_region != (spec.get("physical_region") or product.service_region)
        or body.gpu_model != spec["gpu_model"]
        or body.vram_gb != spec["vram_gb"]
        or body.gpu_count < spec["gpu_count"]
    ):
        raise HTTPException(422, "Resource must match the product specification")
    if body.rights_valid_until <= utcnow():
        raise HTTPException(422, "Resource rights have expired")
    require_supplier_attachment(db, supplier, body.ownership_reference)
    require_supplier_attachment(db, supplier, body.benchmark_reference)
    row = Resource(
        id=str(uuid4()),
        product_id=product.id,
        product_version=product.version,
        **body.model_dump(exclude={"product_id"}),
    )
    db.add(row)
    audit(db, user, "compute_resource", row.id, "REGISTERED")
    db.commit()
    return row


@router.get("/compute/resources", response_model=list[ResourceView])
def list_resources(product_id: UUID, db: DB, user: CurrentUser):
    product = db.get(Product, str(product_id))
    if product is None or product.market != "COMPUTE":
        raise HTTPException(404, "Compute product not found")
    check_supplier_access(db, user, get_supplier(db, product.supplier_id))
    return db.scalars(select(Resource).where(Resource.product_id == product.id).order_by(Resource.id)).all()


@router.post("/compute/resources/{resource_id}/verification", response_model=ResourceView)
def verify_resource(resource_id: UUID, body: VerificationInput, db: DB, user: Operator):
    row = db.get(Resource, str(resource_id), with_for_update=True)
    if row is None:
        raise HTTPException(404, "Compute resource not found")
    if row.verified_at is not None:
        raise HTTPException(409, "Resource already verified")
    if row.rights_valid_until <= utcnow():
        raise HTTPException(409, "Resource rights have expired")
    row.verified_by = user.id
    row.verified_at = utcnow()
    audit(db, user, "compute_resource", row.id, "VERIFIED", {"note": body.note})
    db.commit()
    return row


@router.post("/compute/orders/{order_id}/provisioning", response_model=ProvisionView, status_code=201)
def provision_order(order_id: UUID, body: ProvisionInput, db: DB, user: Operator):
    """Record a supplier instance after opening. The pilot requires its opening evidence."""
    from .attachments import require_order_attachment

    order = get_order(db, user, str(order_id), lock=True)
    require_order_state(order, "FULFILLING")
    if order.market != "COMPUTE":
        raise HTTPException(409, "Order is not for Compute")
    if db.get(Provision, order.id) is not None:
        raise HTTPException(409, "Order already provisioned")
    if body.opened_at is None:
        raise HTTPException(422, "Opening time, delivery owner and opening attachment are required")
    if body.opened_at > utcnow() or body.opened_at >= body.ends_at:
        raise HTTPException(422, "Opening time must have elapsed and precede the end of the reserved window")
    require_order_attachment(db, order, f"attachment:{body.opening_attachment_id}", "DELIVERY")
    reservation = None
    if order.snapshot.get("compute_schedule") is not None:
        reservation = db.get(Reservation, order.id)
        if reservation is None or reservation.status != "CONFIRMED":
            raise HTTPException(409, "A confirmed Compute reservation is required")
        if (str(body.resource_id), body.starts_at, body.ends_at) != (
            reservation.resource_id, reservation.starts_at, reservation.ends_at,
        ):
            raise HTTPException(422, "Provisioning must use the confirmed resource and exact scheduled window")
        require_schedule_rights(db, order, reservation.ends_at)
    else:
        get_published_product(db, order.snapshot["product_id"], lock=True)
    resource = db.get(Resource, str(body.resource_id), with_for_update=True)
    if resource is None:
        raise HTTPException(404, "Compute resource not found")
    if (
        resource.verified_at is None
        or resource.product_id != order.snapshot["product_id"]
    ):
        raise HTTPException(409, "A verified resource for the ordered product is required")
    if body.starts_at >= body.ends_at or body.ends_at > resource.rights_valid_until:
        raise HTTPException(422, "Provisioning period must be within the resource rights validity")
    gpu_count = reservation.gpu_count if reservation is not None else order.snapshot["specification"]["gpu_count"]
    if order.snapshot["unit"] == "GPU_HOUR":
        duration = body.ends_at - body.starts_at
        microseconds = (duration.days * 86400 + duration.seconds) * 1_000_000 + duration.microseconds
        if Decimal(microseconds) * gpu_count != Decimal(order.snapshot["quantity"]) * 3_600_000_000:
            raise HTTPException(422, "Provisioned GPU-hours must match the order")

    if reservation is not None:
        reservation.status = "PROVISIONED"
    elif not capacity_available(db, resource, body.starts_at, body.ends_at, gpu_count, utcnow()):
        raise HTTPException(409, "Insufficient capacity during the requested period")

    row = Provision(
        order_id=order.id, resource_id=resource.id, gpu_count=gpu_count,
        opening_attachment_id=str(body.opening_attachment_id) if body.opening_attachment_id else None,
        **body.model_dump(exclude={"resource_id", "opening_attachment_id"}),
    )
    db.add(row)
    audit(db, user, "order", order.id, "COMPUTE_PROVISIONED", {"resource_id": resource.id})
    db.commit()
    return row


@router.get("/compute/orders/{order_id}/provisioning", response_model=ProvisionView)
def get_provision(order_id: UUID, db: DB, user: CurrentUser):
    order = get_order(db, user, str(order_id))
    row = db.get(Provision, order.id)
    if row is None:
        raise HTTPException(404, "Compute provisioning not found")
    return row


@router.post("/compute/orders/{order_id}/sla-events", response_model=SLAView, status_code=201)
def record_sla(order_id: UUID, body: SLAInput, db: DB, user: CurrentUser):
    from .attachments import require_order_attachment

    order = get_order(db, user, str(order_id))
    check_supplier_access(db, user, get_supplier(db, order.supplier_id))
    provision = db.get(Provision, order.id)
    if provision is None:
        raise HTTPException(404, "Compute provisioning not found")
    if not provision.starts_at <= body.occurred_at <= min(provision.ends_at, utcnow()):
        raise HTTPException(422, "Event must fall within elapsed provisioned time")
    require_order_attachment(db, order, body.evidence_ref, "DELIVERY")
    event = SLAEvent(id=str(uuid4()), order_id=order.id, **body.model_dump())
    db.add(event)
    audit(db, user, "order", order.id, "COMPUTE_SLA_EVENT", {"event_id": event.id, "kind": event.kind})
    db.commit()
    return event


@router.get("/compute/orders/{order_id}/sla-events", response_model=list[SLAView])
def list_sla(order_id: UUID, db: DB, user: CurrentUser):
    order = get_order(db, user, str(order_id))
    return db.scalars(
        select(SLAEvent).where(SLAEvent.order_id == order.id).order_by(SLAEvent.occurred_at, SLAEvent.id)
    ).all()


def require_fulfillment_evidence(db: Session, order: Order) -> None:
    if order.market == "COMPUTE":
        if db.get(Provision, order.id) is None:
            raise HTTPException(409, "Verified Compute provisioning is required")
        return

    from .token import Usage

    states = set(db.scalars(select(Usage.status).where(Usage.order_id == order.id).distinct()))
    if "RECORDED" not in states or states.intersection({"PENDING", "REVIEW_REQUIRED"}):
        raise HTTPException(409, "Settled Token usage evidence is required")
