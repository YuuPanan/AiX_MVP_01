import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from os import environ
from time import monotonic
from typing import Annotated, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import AwareDatetime, BaseModel, Field, StringConstraints
from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, String, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .audit import audit
from .catalog import get_published_product
from .db import Base, DB, utcnow
from .identity import CurrentUser, Operator, require_role
from .procurement import Order, get_order, require_no_approved_refund, require_order_state

router = APIRouter(tags=["token"])
Units = Annotated[int, Field(strict=True, ge=0, le=10_000_000)]
Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
MILLION = Decimal(1_000_000)
CHAT_UNITS = {"INPUT_TOKEN", "OUTPUT_TOKEN", "TOKEN", "REQUEST"}


class Usage(Base):
    __tablename__ = "token_usage"
    __table_args__ = (UniqueConstraint("order_id", "idempotency_key", name="uq_token_usage_idempotency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20))
    requested_model: Mapped[str] = mapped_column(String(200))
    provider_model: Mapped[str | None] = mapped_column(String(200))
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    upstream_usage: Mapped[dict | None] = mapped_column(JSON)
    unit: Mapped[str] = mapped_column(String(30))
    units: Mapped[int | None]
    billable_units: Mapped[int | None]
    amount: Mapped[Decimal | None] = mapped_column(Numeric(29, 8))
    currency: Mapped[str] = mapped_column(String(3))
    provider_cost: Mapped[Decimal | None] = mapped_column(Numeric(28, 14))
    provider_currency: Mapped[str] = mapped_column(String(3))
    provider_input_rate: Mapped[Decimal] = mapped_column(Numeric(16, 8))
    provider_output_rate: Mapped[Decimal] = mapped_column(Numeric(16, 8))
    elapsed_ms: Mapped[int | None]
    reconciliation_reference: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: Annotated[str, Field(min_length=1, max_length=8000)]


class ChatInput(BaseModel):
    order_id: UUID
    messages: Annotated[list[Message], Field(min_length=1, max_length=16)]
    max_output_tokens: Annotated[int, Field(ge=1, le=2048)] = 256


class ProviderUsage(BaseModel):
    prompt_tokens: Units
    completion_tokens: Units
    total_tokens: Annotated[int, Field(strict=True, ge=0, le=20_000_000)] | None = None


class ProviderChoice(BaseModel):
    message: Message
    finish_reason: str | None = None


class ProviderChat(BaseModel):
    id: Reference
    model: Reference
    choices: Annotated[list[ProviderChoice], Field(min_length=1, max_length=1)]
    usage: ProviderUsage


class UsageView(BaseModel):
    id: str
    order_id: str
    idempotency_key: str
    status: str
    requested_model: str
    provider_model: str | None
    provider_request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    upstream_usage: ProviderUsage | None
    unit: str
    units: int | None
    billable_units: int | None
    amount: Decimal | None
    currency: str
    elapsed_ms: int | None
    reconciliation_reference: str | None
    created_at: AwareDatetime
    settled_at: AwareDatetime | None


class OperatorUsageView(UsageView):
    provider_cost: Decimal | None
    provider_currency: str
    provider_input_rate: Decimal
    provider_output_rate: Decimal


class ChatView(BaseModel):
    output: Message
    finish_reason: str | None
    usage: UsageView


class Reconciliation(BaseModel):
    outcome: Literal["RECORDED", "NO_CHARGE"]
    evidence_ref: Reference
    provider_request_id: Reference | None = None
    provider_model: Reference | None = None
    usage: ProviderUsage | None = None


@dataclass(frozen=True)
class ProviderConfig:
    endpoint: httpx.URL
    key: str
    model: str
    currency: str
    input_rate: Decimal
    output_rate: Decimal


def provider_settings(product_id: str) -> ProviderConfig:
    if environ.get("AIX_TOKEN_PRODUCT_ID") != product_id:
        raise HTTPException(503, "No pilot provider configured for this product")
    try:
        endpoint = httpx.URL(environ["AIX_TOKEN_PROVIDER_URL"])
        key = environ["AIX_TOKEN_PROVIDER_KEY"]
        model = environ["AIX_TOKEN_PROVIDER_MODEL"]
        currency = environ["AIX_TOKEN_PROVIDER_CURRENCY"]
        input_rate = Decimal(environ["AIX_TOKEN_INPUT_RATE_PER_MILLION"])
        output_rate = Decimal(environ["AIX_TOKEN_OUTPUT_RATE_PER_MILLION"])
        if (
            endpoint.scheme != "https" or not endpoint.host or endpoint.userinfo or endpoint.fragment
            or endpoint.query or not key.strip() or not 1 <= len(model.strip()) <= 200
            or currency not in {"USD", "CNY"}
            or any(
                not rate.is_finite() or not 0 <= rate < Decimal("100000000")
                or rate != rate.quantize(Decimal("0.00000001"))
                for rate in (input_rate, output_rate)
            )
        ):
            raise ValueError
    except (KeyError, ValueError, InvalidOperation, httpx.InvalidURL):
        raise HTTPException(503, "Pilot provider configuration is incomplete or invalid") from None
    return ProviderConfig(endpoint, key, model.strip(), currency, input_rate, output_rate)


def call_provider(
    config: ProviderConfig, messages: list[Message], max_tokens: int
) -> ProviderChat:
    started = monotonic()
    payload = {
        "model": config.model,
        "messages": [message.model_dump() for message in messages],
        "max_tokens": max_tokens,
        "stream": False,
    }
    with httpx.stream(
        "POST", config.endpoint, headers={"Authorization": f"Bearer {config.key}"},
        json=payload, timeout=10, trust_env=False, follow_redirects=False,
    ) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_bytes(chunk_size=8192):
            data.extend(chunk)
            if len(data) > 512_000 or monotonic() - started > 45:
                raise ValueError("Upstream response exceeded pilot limits")
    result = ProviderChat.model_validate(json.loads(data))
    if result.model != config.model or result.choices[0].message.role != "assistant":
        raise ValueError("Unexpected upstream response")
    return result


def provider_adapter() -> tuple[
    Callable[[str], ProviderConfig],
    Callable[[ProviderConfig, list[Message], int], ProviderChat],
]:
    return provider_settings, call_provider


def consumed_units(db: Session, order: Order) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(Usage.units), 0))
        .where(Usage.order_id == order.id, Usage.status == "RECORDED")
    )


def record_usage(
    db: Session, order: Order, row: Usage, usage: ProviderUsage, model: str, provider_id: str
) -> None:
    units = {
        "INPUT_TOKEN": usage.prompt_tokens,
        "OUTPUT_TOKEN": usage.completion_tokens,
        "TOKEN": usage.prompt_tokens + usage.completion_tokens,
        "REQUEST": 1,
    }[row.unit]
    remaining = max(0, int(Decimal(order.snapshot["quantity"])) - consumed_units(db, order))
    row.status = "RECORDED"
    row.provider_model = model
    row.provider_request_id = provider_id
    row.input_tokens = usage.prompt_tokens
    row.output_tokens = usage.completion_tokens
    row.upstream_usage = usage.model_dump(exclude_none=True)
    row.units = units
    row.billable_units = min(remaining, units)
    row.amount = row.billable_units * Decimal(order.snapshot["unit_price"])
    row.provider_cost = (
        usage.prompt_tokens * row.provider_input_rate + usage.completion_tokens * row.provider_output_rate
    ) / MILLION
    row.settled_at = utcnow()


@router.post("/model-gateway/{model_alias}/chat/completions", response_model=ChatView)
def chat(
    model_alias: Reference,
    body: ChatInput,
    db: DB,
    user: CurrentUser,
    key: Annotated[UUID, Header(alias="Idempotency-Key")],
    provider=Depends(provider_adapter),
):
    order = get_order(db, user, str(body.order_id), lock=True)
    require_role(db, user, order.buyer_organization_id, "OWNER", "BUYER")
    require_order_state(order, "FULFILLING")
    spec = order.snapshot["specification"]
    unit = order.snapshot["unit"]
    if (
        order.market != "TOKEN_API" or spec.get("api_type") != "TEXT"
        or spec.get("model") != model_alias or unit not in CHAT_UNITS
    ):
        raise HTTPException(409, "Order does not match this text API")
    if sum(len(message.content) for message in body.messages) > 16_000:
        raise HTTPException(422, "Messages exceed the pilot input limit")
    product = get_published_product(db, order.snapshot["product_id"], lock=True)
    if db.scalar(select(Usage.id).where(Usage.order_id == order.id, Usage.idempotency_key == str(key))):
        raise HTTPException(409, "Request already recorded. See the order usage records.")
    unresolved = db.scalar(
        select(Usage.id).where(Usage.order_id == order.id, Usage.status.in_(("PENDING", "REVIEW_REQUIRED")))
    )
    if unresolved:
        raise HTTPException(409, "Previous request is still pending or requires reconciliation")
    require_no_approved_refund(db, order)
    remaining = int(Decimal(order.snapshot["quantity"])) - consumed_units(db, order)
    if remaining <= 0:
        raise HTTPException(409, "Order usage limit reached")
    # shortcut: input tokens are measured upstream, any overage is recorded and capped at the order quantity.
    max_tokens = min(body.max_output_tokens, remaining) if unit in {"OUTPUT_TOKEN", "TOKEN"} else body.max_output_tokens
    settings, send = provider
    config = settings(product.id)
    row = Usage(
        id=str(uuid4()), order_id=order.id, idempotency_key=str(key), requested_model=model_alias,
        status="PENDING", unit=unit, currency=order.currency, provider_currency=config.currency,
        provider_input_rate=config.input_rate, provider_output_rate=config.output_rate,
    )
    usage_id, order_id = row.id, order.id
    db.add(row)
    audit(db, user, "order", order_id, "TOKEN_CALL_RESERVED", {"usage_id": usage_id})
    db.commit()

    started = monotonic()
    try:
        result = send(config, body.messages, max_tokens)
    except (httpx.HTTPError, ValueError):
        result = None
    elapsed_ms = int((monotonic() - started) * 1000)

    order = db.get(Order, order_id, with_for_update=True)
    row = db.get(Usage, usage_id, with_for_update=True)
    if row.status != "PENDING":
        raise HTTPException(409, "Request already reconciled")
    row.elapsed_ms = elapsed_ms
    if result is None:
        row.status = "REVIEW_REQUIRED"
        audit(db, user, "order", order_id, "TOKEN_CALL_REVIEW_REQUIRED", {"usage_id": usage_id})
        db.commit()
        raise HTTPException(502, "Provider response could not be verified. Reconciliation required.")
    record_usage(db, order, row, result.usage, result.model, result.id)
    audit(db, user, "order", order_id, "TOKEN_USAGE_RECORDED", {"usage_id": usage_id})
    db.commit()
    return {"output": result.choices[0].message, "finish_reason": result.choices[0].finish_reason, "usage": row}


@router.post("/admin/token/usage/{usage_id}/reconciliation", response_model=OperatorUsageView)
def reconcile_usage(usage_id: UUID, body: Reconciliation, db: DB, user: Operator):
    row = db.get(Usage, str(usage_id))
    if row is None:
        raise HTTPException(404, "Usage record not found")
    order = db.get(Order, row.order_id, with_for_update=True)
    db.refresh(row, with_for_update=True)
    if not (
        row.status == "REVIEW_REQUIRED"
        or (row.status == "PENDING" and row.created_at < utcnow() - timedelta(minutes=2))
    ):
        raise HTTPException(409, "Usage is active or has already been settled")
    if body.outcome == "RECORDED":
        if body.usage is None or body.provider_model is None or body.provider_request_id is None:
            raise HTTPException(422, "Upstream usage, model and request ID are required")
        record_usage(db, order, row, body.usage, body.provider_model, body.provider_request_id)
    else:
        if any(value is not None for value in (body.usage, body.provider_model, body.provider_request_id)):
            raise HTTPException(422, "Usage fields must be omitted for a no-charge reconciliation")
        row.status = "NO_CHARGE"
        row.settled_at = utcnow()
    row.reconciliation_reference = body.evidence_ref
    audit(db, user, "order", order.id, "TOKEN_CALL_RECONCILED", {"usage_id": row.id, "outcome": body.outcome})
    db.commit()
    return row


@router.get("/usage/records", response_model=list[UsageView])
def usage_records(
    order_id: UUID, db: DB, user: CurrentUser,
    offset: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
):
    order = get_order(db, user, str(order_id))
    return db.scalars(
        select(Usage).where(Usage.order_id == order.id).order_by(Usage.created_at, Usage.id).offset(offset).limit(limit)
    ).all()


@router.get("/admin/token/usage", response_model=list[OperatorUsageView])
def operator_usage_records(
    order_id: UUID, db: DB, user: Operator,
    offset: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
):
    return usage_records(order_id, db, user, offset, limit)


@router.get("/usage/summary")
def usage_summary(order_id: UUID, db: DB, user: CurrentUser):
    order = get_order(db, user, str(order_id))
    if order.market != "TOKEN_API":
        raise HTTPException(404, "Token order not found")
    inputs, outputs, units, billed, amount = db.execute(
        select(
            func.coalesce(func.sum(Usage.input_tokens), 0),
            func.coalesce(func.sum(Usage.output_tokens), 0),
            func.coalesce(func.sum(Usage.units), 0),
            func.coalesce(func.sum(Usage.billable_units), 0),
            func.coalesce(func.sum(Usage.amount), 0),
        ).where(Usage.order_id == order.id, Usage.status == "RECORDED")
    ).one()
    return {
        "order_id": order.id, "input_tokens": inputs, "output_tokens": outputs, "units": units,
        "billable_units": billed, "unit": order.snapshot["unit"], "usage_amount": str(amount),
        "currency": order.currency,
    }
