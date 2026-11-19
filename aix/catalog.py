from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_serializer, model_validator
from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, String, UniqueConstraint, exists, func, or_, select
from sqlalchemy.orm import Mapped, Session, contains_eager, mapped_column, relationship

from .audit import audit
from .db import Base, DB, utcnow
from .identity import CurrentUser, Operator, Organization, User, require_role


Market = Literal["TOKEN_API", "COMPUTE"]
Currency = Literal["USD", "CNY"]
SupplierStatus = Literal["DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "SUSPENDED"]
EvidenceKind = Literal["TOKEN_AUTHORIZATION", "COMPUTE_RIGHTS", "BENCHMARK"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Region = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
PriceBound = Annotated[Decimal | None, Query(ge=0, max_digits=20, decimal_places=8)]
RIGHTS_KIND = {"TOKEN_API": "TOKEN_AUTHORIZATION", "COMPUTE": "COMPUTE_RIGHTS"}
TOKEN_UNITS = ("INPUT_TOKEN", "OUTPUT_TOKEN", "TOKEN", "IMAGE", "REQUEST", "SECOND")
PILOT_TOKEN_UNITS = ("INPUT_TOKEN", "OUTPUT_TOKEN", "TOKEN", "REQUEST")
COMPUTE_UNITS = ("GPU_HOUR", "INSTANCE", "RESERVATION", "SERVICE")


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("organization_id", "market"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    market: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), index=True)
    review_reason: Mapped[str | None] = mapped_column(String(500))


class SupplierEvidence(Base):
    __tablename__ = "supplier_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    reference: Mapped[str] = mapped_column(String(500))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    market: Mapped[str] = mapped_column(String(16))
    specification: Mapped[dict] = mapped_column(JSON)
    unit: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    service_region: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[int] = mapped_column(default=1)
    publication_evidence_id: Mapped[str | None] = mapped_column(ForeignKey("supplier_evidence.id"))
    supplier: Mapped[Supplier] = relationship()

    @property
    def supplier_name(self) -> str:
        return self.supplier.name


class SupplierInput(BaseModel):
    organization_id: str
    market: Market
    name: Name


class SupplierView(SupplierInput):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: SupplierStatus
    review_reason: str | None


class SupplierPublicView(BaseModel):
    id: str
    name: str
    market: Market
    verified: Literal[True] = True


class EvidenceInput(BaseModel):
    kind: EvidenceKind
    reference: Reference
    valid_until: AwareDatetime | None = None

    @model_validator(mode="after")
    def verify_validity(self):
        if self.kind != "BENCHMARK" and self.valid_until is None:
            raise ValueError("Rights evidence requires a validity deadline")
        if self.valid_until is not None and self.valid_until <= utcnow():
            raise ValueError("Evidence validity deadline must be in the future")
        return self


class EvidenceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    supplier_id: str
    kind: EvidenceKind
    reference: str
    valid_until: datetime | None
    reviewed_at: datetime | None


class ReviewInput(BaseModel):
    decision: Literal["APPROVED", "REJECTED", "SUSPENDED"]
    reason: Reference


class TokenSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Name
    model_version: Name
    api_type: Literal["TEXT", "IMAGE"] = "TEXT"


class ComputeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gpu_model: Name
    gpu_count: Annotated[int, Field(strict=True, gt=0, le=10_000)]
    vram_gb: Annotated[int, Field(strict=True, gt=0, le=1024)]
    physical_region: Region | None = None


class ProductInput(BaseModel):
    supplier_id: str
    name: Name
    market: Market
    specification: TokenSpec | ComputeSpec
    unit: str
    currency: Currency
    unit_price: Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=8)]
    service_region: Region

    @model_validator(mode="after")
    def verify_market_fields(self):
        if self.market == "TOKEN_API" and isinstance(self.specification, TokenSpec) and self.unit in TOKEN_UNITS:
            return self
        if self.market == "COMPUTE" and isinstance(self.specification, ComputeSpec) and self.unit in COMPUTE_UNITS:
            return self
        raise ValueError("Specification and native billing unit must match the market")


class ProductView(ProductInput):
    model_config = ConfigDict(from_attributes=True)

    id: str
    supplier_name: str
    status: Literal["DRAFT", "PUBLISHED"]
    version: int

    @field_serializer("unit_price")
    def serialize_price(self, unit_price: Decimal) -> str:
        return format(unit_price, "f")


class PublishInput(BaseModel):
    evidence_id: str | None = None


def get_supplier(db: Session, supplier_id: str, *, lock: bool = False) -> Supplier:
    supplier = db.get(Supplier, supplier_id, with_for_update=lock)
    if supplier is None:
        raise HTTPException(404, "Supplier not found")
    return supplier


def check_supplier_access(db: Session, user: User, supplier: Supplier) -> None:
    if not user.is_operator:
        require_role(db, user, supplier.organization_id, "OWNER", "SUPPLIER")


def rights_evidence(supplier: Supplier, now: datetime):
    return select(SupplierEvidence).where(
        SupplierEvidence.supplier_id == supplier.id,
        SupplierEvidence.kind == RIGHTS_KIND[supplier.market],
        SupplierEvidence.valid_until > now,
    )


def published_products():
    return select(Product).join(Product.supplier).options(contains_eager(Product.supplier)).where(
        Product.status == "PUBLISHED", Supplier.status == "APPROVED",
        exists().where(
            SupplierEvidence.id == Product.publication_evidence_id,
            SupplierEvidence.supplier_id == Supplier.id,
            SupplierEvidence.reviewed_at.is_not(None),
            SupplierEvidence.valid_until > utcnow(),
        ),
    )


def get_published_product(db: Session, product_id: str, *, lock: bool = False) -> Product:
    if lock:
        supplier_id = db.scalar(select(Product.supplier_id).where(Product.id == product_id))
        if supplier_id is None:
            raise HTTPException(404, "Product not available")
        get_supplier(db, supplier_id, lock=True)
    query = published_products().where(Product.id == product_id)
    if lock:
        query = query.with_for_update(of=Product)
    product = db.scalar(query)
    if product is None:
        raise HTTPException(404, "Product not available")
    return product


router = APIRouter(tags=["Supplier and catalog"])


@router.post("/suppliers", response_model=SupplierView, status_code=201)
def register_supplier(body: SupplierInput, db: DB, user: CurrentUser) -> Supplier:
    require_role(db, user, body.organization_id, "OWNER", "SUPPLIER")
    # Lock the organization before enforcing one supplier per market.
    db.get(Organization, body.organization_id, with_for_update=True)
    if db.scalar(select(Supplier.id).where(
        Supplier.organization_id == body.organization_id, Supplier.market == body.market,
    )) is not None:
        raise HTTPException(409, "A supplier already exists in this market for the organization")
    supplier = Supplier(id=str(uuid4()), status="DRAFT", **body.model_dump())
    db.add(supplier)
    audit(db, user, "supplier", supplier.id, "supplier.created")
    db.commit()
    return supplier


@router.get("/suppliers", response_model=list[SupplierView])
def list_suppliers(organization_id: str, db: DB, user: CurrentUser) -> list[Supplier]:
    require_role(db, user, organization_id, "OWNER", "SUPPLIER")
    return list(db.scalars(select(Supplier).where(Supplier.organization_id == organization_id).order_by(Supplier.id)))


@router.get("/suppliers/{supplier_id}", response_model=SupplierView)
def supplier_details(supplier_id: str, db: DB, user: CurrentUser) -> Supplier:
    supplier = get_supplier(db, supplier_id)
    check_supplier_access(db, user, supplier)
    return supplier


@router.post("/suppliers/{supplier_id}/evidences", response_model=EvidenceView, status_code=201)
def add_evidence(supplier_id: str, body: EvidenceInput, db: DB, user: CurrentUser) -> SupplierEvidence:
    from .attachments import require_supplier_attachment

    supplier = get_supplier(db, supplier_id, lock=True)
    check_supplier_access(db, user, supplier)
    if supplier.status == "SUBMITTED":
        raise HTTPException(409, "Submitted evidence is being reviewed")
    if body.kind != RIGHTS_KIND[supplier.market] and (supplier.market, body.kind) != ("COMPUTE", "BENCHMARK"):
        raise HTTPException(422, "Evidence kind must match the supplier market")
    require_supplier_attachment(db, supplier, body.reference)
    evidence = SupplierEvidence(id=str(uuid4()), supplier_id=supplier.id, **body.model_dump())
    db.add(evidence)
    audit(db, user, "supplier", supplier.id, "supplier.evidence_added", {
        "evidence_id": evidence.id, "kind": evidence.kind,
    })
    db.commit()
    return evidence


@router.get("/suppliers/{supplier_id}/evidences", response_model=list[EvidenceView])
def list_evidence(supplier_id: str, db: DB, user: CurrentUser) -> list[SupplierEvidence]:
    supplier = get_supplier(db, supplier_id)
    check_supplier_access(db, user, supplier)
    return list(db.scalars(select(SupplierEvidence).where(
        SupplierEvidence.supplier_id == supplier.id,
    ).order_by(SupplierEvidence.id)))


@router.post("/suppliers/{supplier_id}/submit", response_model=SupplierView)
def submit_supplier(supplier_id: str, db: DB, user: CurrentUser) -> Supplier:
    supplier = get_supplier(db, supplier_id, lock=True)
    check_supplier_access(db, user, supplier)
    if supplier.status not in ("DRAFT", "REJECTED", "APPROVED"):
        raise HTTPException(409, "Supplier cannot submit for review in this state")
    if db.scalar(rights_evidence(supplier, utcnow()).limit(1)) is None:
        raise HTTPException(409, "Current rights evidence is required")
    supplier.status = "SUBMITTED"
    supplier.review_reason = None
    audit(db, user, "supplier", supplier.id, "supplier.submitted")
    db.commit()
    return supplier


@router.get("/admin/suppliers", response_model=list[SupplierView])
def supplier_review_queue(
    db: DB, user: Operator, status: SupplierStatus = "SUBMITTED",
    limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Supplier]:
    return list(db.scalars(select(Supplier).where(
        Supplier.status == status,
    ).order_by(Supplier.id).limit(limit).offset(offset)))


@router.post("/admin/suppliers/{supplier_id}/review", response_model=SupplierView)
def review_supplier(supplier_id: str, body: ReviewInput, db: DB, user: Operator) -> Supplier:
    supplier = get_supplier(db, supplier_id, lock=True)
    allowed_states = {"APPROVED": ("SUBMITTED", "SUSPENDED"), "REJECTED": ("SUBMITTED",), "SUSPENDED": ("APPROVED",)}
    if supplier.status not in allowed_states[body.decision]:
        raise HTTPException(409, "Supplier review decision is not valid in this state")
    previous_status = supplier.status
    reviewed_ids = []
    if body.decision == "APPROVED":
        now = utcnow()
        current_evidence = list(db.scalars(rights_evidence(supplier, now)))
        if not current_evidence:
            raise HTTPException(409, "Current rights evidence is required")
        for evidence in current_evidence:
            evidence.reviewed_at = now
            reviewed_ids.append(evidence.id)
    supplier.status = body.decision
    supplier.review_reason = body.reason
    audit(db, user, "supplier", supplier.id, "supplier.reviewed", {
        "previous_status": previous_status, "decision": body.decision, "reason": body.reason,
        "evidence_ids": reviewed_ids,
    })
    db.commit()
    return supplier


@router.post("/products", response_model=ProductView, status_code=201)
def create_product(body: ProductInput, db: DB, user: CurrentUser) -> Product:
    supplier = get_supplier(db, body.supplier_id)
    check_supplier_access(db, user, supplier)
    if supplier.market != body.market:
        raise HTTPException(422, "Product market must match the supplier's market")
    product = Product(id=str(uuid4()), status="DRAFT", **body.model_dump())
    if product.market == "COMPUTE" and product.specification["physical_region"] is None:
        product.specification["physical_region"] = product.service_region
    db.add(product)
    audit(db, user, "product", product.id, "product.created")
    db.commit()
    return product


@router.get("/suppliers/{supplier_id}/products", response_model=list[ProductView])
def list_supplier_products(supplier_id: str, db: DB, user: CurrentUser) -> list[Product]:
    supplier = get_supplier(db, supplier_id)
    check_supplier_access(db, user, supplier)
    return list(db.scalars(select(Product).where(Product.supplier_id == supplier.id).order_by(Product.id)))


@router.post("/products/{product_id}/publish", response_model=ProductView)
def publish_product(product_id: str, db: DB, user: Operator, body: PublishInput | None = None) -> Product:
    supplier_id = db.scalar(select(Product.supplier_id).where(Product.id == product_id))
    if supplier_id is None:
        raise HTTPException(404, "Product not found")
    supplier = get_supplier(db, supplier_id, lock=True)
    product = db.get(Product, product_id, with_for_update=True)
    if supplier.status != "APPROVED":
        raise HTTPException(409, "Supplier approval is required")
    # shortcut: publish only the implemented text adapter, expand when the gateway supports other modalities and units.
    if product.market == "TOKEN_API" and (
        product.specification["api_type"] != "TEXT" or product.unit not in PILOT_TOKEN_UNITS
    ):
        raise HTTPException(409, "The pilot supports text API products billed by tokens or requests")
    evidence_query = rights_evidence(supplier, utcnow()).where(SupplierEvidence.reviewed_at.is_not(None))
    if body and body.evidence_id:
        evidence_query = evidence_query.where(SupplierEvidence.id == body.evidence_id)
    verified = list(db.scalars(evidence_query.limit(2)))
    if not verified:
        raise HTTPException(409, "Active reviewed rights evidence is required")
    if len(verified) > 1:
        raise HTTPException(422, "Select the evidence covering this product")
    evidence_id = verified[0].id
    if product.status == "PUBLISHED" and product.publication_evidence_id == evidence_id:
        return product
    if product.publication_evidence_id is not None:
        product.version += 1
    product.status = "PUBLISHED"
    product.publication_evidence_id = evidence_id
    audit(db, user, "product", product.id, "product.published", {"evidence_id": evidence_id})
    db.commit()
    return product


@router.get("/products", response_model=list[ProductView])
def list_products(
    db: DB, market: Market | None = None, service_region: Region | None = None,
    physical_region: Region | None = None, gpu_model: Name | None = None,
    currency: Currency | None = None, unit: Annotated[str | None, Query(max_length=16)] = None,
    min_price: PriceBound = None, max_price: PriceBound = None,
    sort: Literal["name", "price_asc", "price_desc"] = "name",
    q: Annotated[str | None, Query(max_length=120, description="Product, supplier, GPU model, or API model name")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Product]:
    if unit is not None and unit not in TOKEN_UNITS + COMPUTE_UNITS:
        raise HTTPException(422, "Unknown billing unit")
    if (min_price is not None or max_price is not None or sort != "name") and (currency is None or unit is None):
        raise HTTPException(422, "Price filters and sorting require a currency and billing unit")
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(422, "Minimum price exceeds maximum price")

    query = published_products()
    if market is not None:
        query = query.where(Product.market == market)
    if service_region is not None:
        query = query.where(Product.service_region == service_region)
    if physical_region is not None:
        query = query.where(func.lower(Product.specification["physical_region"].as_string()) == physical_region.lower())
    if gpu_model is not None:
        query = query.where(func.lower(Product.specification["gpu_model"].as_string()) == gpu_model.lower())
    if currency is not None:
        query = query.where(Product.currency == currency)
    if unit is not None:
        query = query.where(Product.unit == unit)
    if min_price is not None:
        query = query.where(Product.unit_price >= min_price)
    if max_price is not None:
        query = query.where(Product.unit_price <= max_price)
    if q and (term := q.strip()):
        query = query.where(or_(
            Product.name.icontains(term, autoescape=True),
            Supplier.name.icontains(term, autoescape=True),
            Product.specification["gpu_model"].as_string().icontains(term, autoescape=True),
            Product.specification["model"].as_string().icontains(term, autoescape=True),
        ))

    if sort == "price_asc":
        query = query.order_by(Product.unit_price.asc())
    if sort == "price_desc":
        query = query.order_by(Product.unit_price.desc())
    return list(db.scalars(query.order_by(Product.name, Product.id).limit(limit).offset(offset)))


@router.get("/products/{product_id}", response_model=ProductView)
def product_details(product_id: str, db: DB) -> Product:
    return get_published_product(db, product_id)


@router.get("/suppliers/{supplier_id}/public-profile")
def public_supplier_profile(supplier_id: str, db: DB) -> SupplierPublicView:
    product = db.scalar(published_products().where(Product.supplier_id == supplier_id).limit(1))
    if product is None:
        raise HTTPException(404, "Supplier not found")
    return SupplierPublicView(id=supplier_id, name=product.supplier.name, market=product.supplier.market)
