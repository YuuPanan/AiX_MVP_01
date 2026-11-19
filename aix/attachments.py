import re
import shutil
import socket
import subprocess
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from os import environ, name as os_name
from pathlib import Path
from typing import Annotated, Literal
from unicodedata import category
from urllib.parse import quote, unquote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, LargeBinary, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column
from starlette.concurrency import run_in_threadpool

from .audit import audit
from .catalog import Supplier, check_supplier_access, get_supplier
from .db import Base, DB, utcnow
from .identity import CurrentUser, Membership, User, require_role
from .procurement import BUYERS, SELLERS, Order, get_order

router = APIRouter(tags=["Attachments"])
OrderKind = Literal["CONTRACT", "PAYMENT", "BANK_CREDIT", "DELIVERY", "DISPUTE", "REFUND"]
Kind = Literal[OrderKind, "SUPPLIER_EVIDENCE"]
MAX_BYTES = 5 * 1024 * 1024
REF_PREFIX = "attachment:"
PRIVATE_ORDER_KINDS = ("PAYMENT", "REFUND")
FILE_TYPES = {
    "application/pdf": (("pdf",), b"%PDF-"),
    "image/png": (("png",), b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": (("jpg", "jpeg"), b"\xff\xd8\xff"),
}


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (
        CheckConstraint(
            "(order_id IS NOT NULL AND supplier_id IS NULL AND "
            "kind IN ('CONTRACT', 'PAYMENT', 'BANK_CREDIT', 'DELIVERY', 'DISPUTE', 'REFUND')) OR "
            "(order_id IS NULL AND supplier_id IS NOT NULL AND kind = 'SUPPLIER_EVIDENCE')",
            name="ck_attachment_scope_kind",
        ),
        CheckConstraint(
            f"size BETWEEN 1 AND {MAX_BYTES} AND octet_length(data) = size",
            name="ck_attachment_size",
        ),
        CheckConstraint(
            "content_type IN ('application/pdf', 'image/png', 'image/jpeg')",
            name="ck_attachment_content_type",
        ),
        Index("ix_attachments_order_created", "order_id", "created_at", "id"),
        Index("ix_attachments_supplier_created", "supplier_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"))
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id"))
    kind: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str] = mapped_column(String(120))
    content_type: Mapped[str] = mapped_column(String(32))
    size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)

    @property
    def reference(self) -> str:
        return f"{REF_PREFIX}{self.id}"


class AttachmentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_id: str | None
    supplier_id: str | None
    kind: Kind
    filename: str
    content_type: str
    size: int
    sha256: str
    uploaded_by: str
    created_at: datetime
    reference: str


def attachment_id(reference: str) -> str | None:
    if not reference.lstrip().lower().startswith(REF_PREFIX):
        return None
    value = reference[len(REF_PREFIX):]
    try:
        if reference.startswith(REF_PREFIX) and str(UUID(value)) == value:
            return value
    except ValueError:
        pass
    raise HTTPException(422, "Invalid attachment reference")


def require_order_attachment(db: Session, order: Order, reference: str, kind: OrderKind) -> Attachment | None:
    key = attachment_id(reference)
    if key is None:
        return None
    attachment = db.get(Attachment, key)
    if attachment is None or attachment.order_id != order.id or attachment.kind != kind:
        raise HTTPException(422, "Attachment must belong to this order and evidence kind")
    return attachment


def require_supplier_attachment(db: Session, supplier: Supplier, reference: str) -> Attachment | None:
    key = attachment_id(reference)
    if key is None:
        return None
    attachment = db.get(Attachment, key)
    if attachment is None or attachment.supplier_id != supplier.id or attachment.kind != "SUPPLIER_EVIDENCE":
        raise HTTPException(422, "Attachment must belong to this supplier")
    return attachment


def scan_file(data: bytes) -> None:
    port_setting = environ.get("AIX_CLAMD_PORT")
    if port_setting is not None:
        try:
            port = int(port_setting)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise HTTPException(503, "Attachment scanner is unavailable") from None
        scan_with_clamd(data, port)
        return

    configured = environ.get("AIX_CLAMSCAN")
    executable = shutil.which(configured) if configured else None
    if not executable or (os_name == "nt" and Path(executable).suffix.lower() != ".exe"):
        raise HTTPException(503, "Attachment scanner is unavailable")
    try:
        result = subprocess.run(
            [executable, "--no-summary", "--alert-encrypted=yes", "-"],
            input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise HTTPException(503, "Attachment scan did not complete") from None
    if result.returncode == 1:
        raise HTTPException(422, "Attachment failed the security scan")
    if result.returncode != 0:
        raise HTTPException(503, "Attachment scan did not complete")


def scan_with_clamd(data: bytes, port: int) -> None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=30) as connection:
            connection.sendall(b"zINSTREAM\x00" + len(data).to_bytes(4, "big"))
            connection.sendall(data)
            connection.sendall(b"\x00" * 4)
            with connection.makefile("rb") as records:
                reply = records.read(1024)
    except OSError:
        raise HTTPException(503, "Attachment scan did not complete") from None
    if re.fullmatch(rb"stream: [^\x00\r\n]+ FOUND\x00", reply):
        raise HTTPException(422, "Attachment failed the security scan")
    if reply != b"stream: OK\x00":
        raise HTTPException(503, "Attachment scan did not complete")


def scan_adapter() -> Callable[[bytes], None]:
    return scan_file


Scanner = Annotated[Callable[[bytes], None], Depends(scan_adapter)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


def file_name(encoded: str | None) -> str:
    if not encoded or len(encoded) > 1440 or not encoded.isascii() or re.search(r"%(?![0-9a-fA-F]{2})", encoded):
        raise HTTPException(422, "X-Filename must be a UTF-8 percent-encoded filename")
    try:
        filename = unquote(encoded, encoding="utf-8", errors="strict")
    except UnicodeError:
        raise HTTPException(422, "X-Filename must be valid UTF-8") from None
    if (
        not filename or len(filename) > 120 or filename != filename.strip() or filename.startswith(".")
        or any(char in '/\\:*?"<>|' or category(char) in ("Cc", "Cf", "Cs") for char in filename)
    ):
        raise HTTPException(422, "Unsafe attachment filename")
    return filename


async def read_file(request: Request) -> tuple[str, str, bytes]:
    content_type = request.headers.get("content-type", "").strip().lower()
    if content_type not in FILE_TYPES:
        raise HTTPException(415, "Only PDF, PNG and JPEG attachments are supported")
    filename = file_name(request.headers.get("x-filename"))
    extensions, magic = FILE_TYPES[content_type]
    stem, _, suffix = filename.rpartition(".")
    if not stem or suffix.lower() not in extensions:
        raise HTTPException(415, "Filename extension does not match the attachment type")
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if not length.isascii() or not length.isdigit():
                raise ValueError
            expected_size = int(length)
        except ValueError:
            raise HTTPException(422, "Invalid Content-Length") from None
        if expected_size > MAX_BYTES:
            raise HTTPException(413, "Attachment exceeds 5 MiB")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_BYTES:
            raise HTTPException(413, "Attachment exceeds 5 MiB")
        content.extend(chunk)
    if not content:
        raise HTTPException(422, "Attachment cannot be empty")
    if not content.startswith(magic):
        raise HTTPException(415, "File signature does not match the attachment type")
    return filename, content_type, bytes(content)


def may_read_private_order_docs(
    db: Session, order: Order, user: User, organization_id: str | None = None,
) -> bool:
    if user.is_operator:
        return True
    if organization_id is not None and order.buyer_organization_id != organization_id:
        return False
    member = db.get(Membership, (order.buyer_organization_id, user.id))
    return member is not None and member.role in (*BUYERS, "FINANCE")


def authorize_upload(
    db: Session, user: User, kind: Kind, order_id: str | None, supplier_id: str | None, *, lock: bool = False,
) -> None:
    if order_id is None:
        supplier = get_supplier(db, supplier_id, lock=lock)
        check_supplier_access(db, user, supplier)
        return
    order = get_order(db, user, order_id, lock=lock)
    if user.is_operator:
        return
    if kind in ("CONTRACT", "REFUND", "BANK_CREDIT"):
        raise HTTPException(403, "Operator access required")
    if kind == "PAYMENT" and not may_read_private_order_docs(db, order, user):
        raise HTTPException(403, "Only the buying team can upload payment evidence")
    if kind == "DELIVERY":
        require_role(db, user, order.supplier_organization_id, *SELLERS)


async def receive_attachment(
    db: Session, user: User, request: Request, scanner: Scanner, kind: Kind,
    *, order_id: str | None = None, supplier_id: str | None = None,
) -> AttachmentView:
    await run_in_threadpool(authorize_upload, db, user, kind, order_id, supplier_id)
    uploader = user.id
    await run_in_threadpool(db.rollback)
    filename, content_type, content = await read_file(request)
    await run_in_threadpool(scanner, content)
    digest = sha256(content).hexdigest()

    def persist() -> AttachmentView:
        actor = db.get(User, uploader)
        if actor is None:
            raise HTTPException(401, "Uploader is no longer available")
        authorize_upload(db, actor, kind, order_id, supplier_id, lock=True)
        attachment = Attachment(
            id=str(uuid4()), order_id=order_id, supplier_id=supplier_id, kind=kind,
            filename=filename, content_type=content_type, size=len(content), sha256=digest,
            data=content, uploaded_by=actor.id,
        )
        db.add(attachment)
        db.flush()
        audit(db, actor, "attachment", attachment.id, "attachment.uploaded", {
            "kind": kind, "order_id": order_id, "supplier_id": supplier_id,
        })
        view = AttachmentView.model_validate(attachment)
        db.commit()
        return view

    return await run_in_threadpool(persist)


@router.post("/orders/{order_id}/attachments", response_model=AttachmentView, status_code=201)
async def upload_order_attachment(order_id: UUID, kind: OrderKind, request: Request, db: DB, user: CurrentUser, scanner: Scanner):
    return await receive_attachment(db, user, request, scanner, kind, order_id=str(order_id))


@router.post("/suppliers/{supplier_id}/attachments", response_model=AttachmentView, status_code=201)
async def upload_supplier_attachment(supplier_id: UUID, request: Request, db: DB, user: CurrentUser, scanner: Scanner):
    return await receive_attachment(db, user, request, scanner, "SUPPLIER_EVIDENCE", supplier_id=str(supplier_id))


@router.get("/orders/{order_id}/attachments", response_model=list[AttachmentView])
def list_order_attachments(
    order_id: UUID, db: DB, user: CurrentUser, limit: Limit = 50, offset: Offset = 0, organization_id: str | None = None,
):
    order = get_order(db, user, str(order_id), organization_id=organization_id)
    query = select(Attachment).where(Attachment.order_id == order.id)
    if not user.is_operator:
        query = query.where(Attachment.kind != "BANK_CREDIT")
    if not may_read_private_order_docs(db, order, user, organization_id):
        query = query.where(Attachment.kind.not_in(PRIVATE_ORDER_KINDS))
    return db.scalars(query.order_by(Attachment.created_at.desc(), Attachment.id.desc()).limit(limit).offset(offset)).all()


@router.get("/suppliers/{supplier_id}/attachments", response_model=list[AttachmentView])
def list_supplier_attachments(supplier_id: UUID, db: DB, user: CurrentUser, limit: Limit = 50, offset: Offset = 0):
    supplier = get_supplier(db, str(supplier_id))
    check_supplier_access(db, user, supplier)
    query = select(Attachment).where(Attachment.supplier_id == supplier.id)
    return db.scalars(query.order_by(Attachment.created_at.desc(), Attachment.id.desc()).limit(limit).offset(offset)).all()


def download(db: Session, user: User, attachment: Attachment) -> Response:
    content = attachment.data
    content_type = attachment.content_type
    extension = FILE_TYPES[content_type][0][0]
    filename = quote(attachment.filename, safe="")
    audit(db, user, "attachment", attachment.id, "attachment.downloaded", {
        "kind": attachment.kind, "order_id": attachment.order_id, "supplier_id": attachment.supplier_id,
    })
    db.commit()
    return Response(content, media_type=content_type, headers={
        "Content-Disposition": f"attachment; filename=\"attachment.{extension}\"; filename*=UTF-8''{filename}",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/orders/{order_id}/attachments/{file_id}")
def get_order_attachment(order_id: UUID, file_id: UUID, db: DB, user: CurrentUser, organization_id: str | None = None):
    order = get_order(db, user, str(order_id), organization_id=organization_id)
    attachment = db.get(Attachment, str(file_id))
    if (
        attachment is None or attachment.order_id != order.id
        or (attachment.kind == "BANK_CREDIT" and not user.is_operator)
        or (attachment.kind in PRIVATE_ORDER_KINDS and not may_read_private_order_docs(db, order, user, organization_id))
    ):
        raise HTTPException(404, "Attachment not found")
    return download(db, user, attachment)


@router.get("/suppliers/{supplier_id}/attachments/{file_id}")
def get_supplier_attachment(supplier_id: UUID, file_id: UUID, db: DB, user: CurrentUser):
    supplier = get_supplier(db, str(supplier_id))
    check_supplier_access(db, user, supplier)
    attachment = db.get(Attachment, str(file_id))
    if attachment is None or attachment.supplier_id != supplier.id:
        raise HTTPException(404, "Attachment not found")
    return download(db, user, attachment)
