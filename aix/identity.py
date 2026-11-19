import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints, field_validator
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from .audit import audit
from .db import Base, DB, utcnow


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String(100))
    is_operator: Mapped[bool] = mapped_column(default=False)


class LoginSession(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    registration_country: Mapped[str | None] = mapped_column(String(2))


class Membership(Base):
    __tablename__ = "memberships"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(16))


class OrganizationInvitation(Base):
    __tablename__ = "organization_invitations"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    email: Mapped[str] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Password = Annotated[str, StringConstraints(min_length=12, max_length=128)]
MemberRole = Literal["BUYER", "FINANCE", "SUPPLIER"]
OrganizationName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120, pattern=r"^[^\x00]*$"),
]
Country = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
DUMMY_HASH = f"{'0' * 32}:{'0' * 64}"


class Credentials(BaseModel):
    email: EmailStr
    password: Password


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    is_operator: bool


class SessionView(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


class PasswordChange(BaseModel):
    current_password: Password
    new_password: Password


class OrganizationInput(BaseModel):
    name: OrganizationName
    registration_country: Country | None = None


class OrganizationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: OrganizationName | None = None
    registration_country: Country | None = None

    @field_validator("name")
    @classmethod
    def name_cannot_be_cleared(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Organization name cannot be null")
        return value


class OrganizationView(OrganizationInput):
    id: str
    role: Literal["OWNER", "BUYER", "FINANCE", "SUPPLIER"]


class RoleInput(BaseModel):
    role: MemberRole


class InvitationInput(RoleInput):
    email: EmailStr


class InvitationView(BaseModel):
    invitation_code: str
    expires_at: datetime


class InvitationSummary(InvitationInput):
    model_config = ConfigDict(from_attributes=True)

    expires_at: datetime


class InvitationAcceptance(BaseModel):
    invitation_code: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{43}$")]


class MemberView(BaseModel):
    user_id: str
    email: str
    role: Literal["OWNER", "BUYER", "FINANCE", "SUPPLIER"]


def password_digest(password: str, salt: bytes) -> str:
    return hashlib.scrypt(
        password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024**2, dklen=32
    ).hex()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return f"{salt.hex()}:{password_digest(password, salt)}"


def verify_password(password: str, password_hash: str) -> bool:
    salt, expected = password_hash.split(":")
    return secrets.compare_digest(password_digest(password, bytes.fromhex(salt)), expected)


def add_user(db: Session, credentials: Credentials, *, is_operator: bool = False) -> User:
    email = credentials.email.lower()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(409, "Account already exists")
    user = User(
        id=str(uuid4()), email=email, is_operator=is_operator,
        password_hash=hash_password(credentials.password),
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as error:
        if getattr(getattr(error.orig, "diag", None), "constraint_name", None) == "uq_users_email":
            raise HTTPException(409, "Account already exists") from None
        raise
    return user


def create_operator(db: Session, email: str, password: str) -> User:
    user = add_user(db, Credentials(email=email, password=password), is_operator=True)
    audit(db, None, "user", user.id, "operator.created")
    return user


bearer = HTTPBearer(auto_error=False)


def token_digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def active_session(
    db: DB, auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> LoginSession:
    if auth and len(auth.credentials) == 43 and auth.credentials.isascii():
        login_session = db.scalar(select(LoginSession).where(
            LoginSession.token_hash == token_digest(auth.credentials), LoginSession.expires_at > utcnow(),
        ))
        if login_session is not None:
            return login_session
    raise HTTPException(401, "Valid bearer token required", headers={"WWW-Authenticate": "Bearer"})


ActiveSession = Annotated[LoginSession, Depends(active_session)]


def current_user(db: DB, session: ActiveSession) -> User:
    return db.get(User, session.user_id)


CurrentUser = Annotated[User, Depends(current_user)]


def operator(user: CurrentUser) -> User:
    if not user.is_operator:
        raise HTTPException(403, "Operator access required")
    return user


Operator = Annotated[User, Depends(operator)]


def require_role(db: Session, user: User, organization_id: str, *roles: str) -> Membership:
    membership = db.get(Membership, (organization_id, user.id))
    if membership is None:
        raise HTTPException(404, "Organization not found")
    if roles and membership.role not in roles:
        raise HTTPException(403, "Organization role is not permitted")
    return membership


router = APIRouter(tags=["Identity"])


@router.post("/auth/register", response_model=UserView, status_code=201)
def register(body: Credentials, db: DB) -> User:
    user = add_user(db, body)
    audit(db, user, "user", user.id, "user.registered")
    db.commit()
    return user


@router.post("/auth/login")
def login(body: Credentials, db: DB, response: Response) -> SessionView:
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    observed_hash = user.password_hash if user else DUMMY_HASH
    matches = verify_password(body.password, observed_hash)
    if not matches or user is None:
        if user is not None:
            audit(db, None, "user", user.id, "auth.login_failed")
            db.commit()
        raise HTTPException(401, "Invalid login credentials")
    db.refresh(user, with_for_update=True)
    if user.password_hash != observed_hash:
        raise HTTPException(401, "Invalid login credentials")
    access_token = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(hours=8)
    db.add(LoginSession(
        token_hash=token_digest(access_token),
        user_id=user.id, expires_at=expires_at,
    ))
    audit(db, user, "user", user.id, "auth.login")
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return SessionView(access_token=access_token, expires_at=expires_at)


@router.post("/auth/logout", status_code=204)
def logout(db: DB, session: ActiveSession, user: CurrentUser) -> None:
    removed = db.execute(delete(LoginSession).where(LoginSession.token_hash == session.token_hash))
    if removed.rowcount:
        audit(db, user, "user", user.id, "auth.logout")
    db.commit()


@router.post("/auth/password/change", status_code=204)
def change_password(body: PasswordChange, db: DB, user: CurrentUser) -> None:
    db.refresh(user, with_for_update=True)
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Invalid current password")
    if body.current_password == body.new_password:
        raise HTTPException(422, "New password must differ from the current password")
    user.password_hash = hash_password(body.new_password)
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    audit(db, user, "user", user.id, "auth.password_changed")
    db.commit()


@router.get("/users/me", response_model=UserView)
def me(user: CurrentUser) -> User:
    return user


def organization_view(organization: Organization, member: Membership) -> OrganizationView:
    return OrganizationView(
        id=organization.id, name=organization.name,
        registration_country=organization.registration_country, role=member.role,
    )


@router.post("/organizations", status_code=201)
def create_organization(body: OrganizationInput, db: DB, user: CurrentUser) -> OrganizationView:
    organization = Organization(id=str(uuid4()), **body.model_dump())
    db.add(organization)
    db.flush()
    membership = Membership(organization_id=organization.id, user_id=user.id, role="OWNER")
    db.add(membership)
    audit(db, user, "organization", organization.id, "organization.created")
    db.commit()
    return organization_view(organization, membership)


@router.get("/organizations")
def list_organizations(db: DB, user: CurrentUser) -> list[OrganizationView]:
    rows = db.execute(select(Organization, Membership).join(Membership).where(
        Membership.user_id == user.id,
    ).order_by(Organization.name, Organization.id))
    return [organization_view(organization, member) for organization, member in rows]


@router.get("/organizations/{organization_id}")
def get_organization(organization_id: str, db: DB, user: CurrentUser) -> OrganizationView:
    membership = require_role(db, user, organization_id)
    return organization_view(db.get(Organization, organization_id), membership)


@router.patch("/organizations/{organization_id}")
def update_organization(organization_id: str, body: OrganizationPatch, db: DB, user: CurrentUser) -> OrganizationView:
    membership = require_role(db, user, organization_id, "OWNER")
    organization = db.get(Organization, organization_id, with_for_update=True)
    changes = {
        field: value for field, value in body.model_dump(exclude_unset=True).items()
        if getattr(organization, field) != value
    }
    for field, value in changes.items():
        setattr(organization, field, value)
    view = organization_view(organization, membership)
    if changes:
        audit(db, user, "organization", organization_id, "organization.updated", {"fields": sorted(changes)})
        db.commit()
    return view


@router.get("/organizations/{organization_id}/members")
def list_members(organization_id: str, db: DB, user: CurrentUser) -> list[MemberView]:
    require_role(db, user, organization_id, "OWNER")
    rows = db.execute(select(Membership, User.email).join(User, User.id == Membership.user_id).where(
        Membership.organization_id == organization_id,
    ).order_by(User.email))
    return [MemberView(user_id=member.user_id, email=email, role=member.role) for member, email in rows]


def active_invitations(organization_id: str, now: datetime):
    return (
        OrganizationInvitation.organization_id == organization_id,
        OrganizationInvitation.accepted_at.is_(None),
        OrganizationInvitation.revoked_at.is_(None),
        OrganizationInvitation.expires_at > now,
    )


def revoke_pending_invitations(db: Session, organization_id: str, email: str, now: datetime) -> int:
    return db.execute(update(OrganizationInvitation).where(
        *active_invitations(organization_id, now), OrganizationInvitation.email == email,
    ).values(revoked_at=now)).rowcount


@router.get("/organizations/{organization_id}/invitations", response_model=list[InvitationSummary])
def list_invitations(organization_id: str, db: DB, user: CurrentUser):
    require_role(db, user, organization_id, "OWNER")
    return db.scalars(select(OrganizationInvitation)
        .where(*active_invitations(organization_id, utcnow()))
        .distinct(OrganizationInvitation.email)
        .order_by(OrganizationInvitation.email, OrganizationInvitation.expires_at.desc(), OrganizationInvitation.token_hash)
    ).all()


@router.post("/organizations/{organization_id}/invitations", status_code=201)
def create_invitation(
    organization_id: str, body: InvitationInput, db: DB, user: CurrentUser, response: Response,
) -> InvitationView:
    require_role(db, user, organization_id, "OWNER")
    db.get(Organization, organization_id, with_for_update=True)
    email = body.email.lower()
    if db.scalar(select(Membership.user_id).join(User, User.id == Membership.user_id).where(
        Membership.organization_id == organization_id, User.email == email,
    )) is not None:
        raise HTTPException(409, "Account is already a member")
    now = utcnow()
    revoke_pending_invitations(db, organization_id, email, now)
    code = secrets.token_urlsafe(32)
    expires_at = now + timedelta(hours=48)
    db.add(OrganizationInvitation(
        token_hash=token_digest(code), organization_id=organization_id,
        email=email, role=body.role, expires_at=expires_at,
    ))
    audit(db, user, "organization", organization_id, "membership.invited", {"role": body.role})
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return InvitationView(invitation_code=code, expires_at=expires_at)


@router.delete("/organizations/{organization_id}/invitations", status_code=204)
def revoke_invitation(organization_id: str, email: EmailStr, db: DB, user: CurrentUser) -> None:
    require_role(db, user, organization_id, "OWNER")
    db.get(Organization, organization_id, with_for_update=True)
    count = revoke_pending_invitations(db, organization_id, email.lower(), utcnow())
    if not count:
        raise HTTPException(404, "Active invitation not found")
    audit(db, user, "organization", organization_id, "membership.invitation_revoked")
    db.commit()


@router.post("/organization-invitations/accept", status_code=201)
def accept_invitation(body: InvitationAcceptance, db: DB, user: CurrentUser) -> MemberView:
    digest = token_digest(body.invitation_code)
    organization_id = db.scalar(select(OrganizationInvitation.organization_id).where(
        OrganizationInvitation.token_hash == digest, OrganizationInvitation.email == user.email,
    ))
    if organization_id is None:
        raise HTTPException(404, "Invitation not found or expired")
    db.get(Organization, organization_id, with_for_update=True)
    now = utcnow()
    invitation = db.scalar(select(OrganizationInvitation).where(
        *active_invitations(organization_id, now), OrganizationInvitation.email == user.email,
    ).order_by(OrganizationInvitation.expires_at.desc(), OrganizationInvitation.token_hash).limit(1).with_for_update())
    if invitation is None or invitation.token_hash != digest:
        raise HTTPException(404, "Invitation not found or expired")
    if db.get(Membership, (organization_id, user.id)) is not None:
        raise HTTPException(409, "Account is already a member")
    invitation.accepted_at = now
    revoke_pending_invitations(db, organization_id, user.email, now)
    db.add(Membership(organization_id=organization_id, user_id=user.id, role=invitation.role))
    audit(db, user, "organization", organization_id, "membership.accepted", {"role": invitation.role})
    view = MemberView(user_id=user.id, email=user.email, role=invitation.role)
    db.commit()
    return view


@router.patch("/organizations/{organization_id}/members/{member_id}")
def change_role(organization_id: str, member_id: str, body: RoleInput, db: DB, user: CurrentUser) -> MemberView:
    require_role(db, user, organization_id, "OWNER")
    db.get(Organization, organization_id, with_for_update=True)
    member = db.get(Membership, (organization_id, member_id), with_for_update=True)
    if member is None:
        raise HTTPException(404, "Member not found")
    if member.role == "OWNER":
        raise HTTPException(403, "The owner's role cannot be changed")
    member.role = body.role
    audit(db, user, "organization", organization_id, "membership.changed", {
        "user_id": member_id, "role": body.role,
    })
    view = MemberView(user_id=member_id, email=db.get(User, member_id).email, role=member.role)
    db.commit()
    return view


@router.delete("/organizations/{organization_id}/members/{member_id}", status_code=204)
def remove_member(organization_id: str, member_id: str, db: DB, user: CurrentUser) -> None:
    require_role(db, user, organization_id, "OWNER")
    db.get(Organization, organization_id, with_for_update=True)
    member = db.get(Membership, (organization_id, member_id), with_for_update=True)
    if member is None:
        raise HTTPException(404, "Member not found")
    if member.role == "OWNER":
        raise HTTPException(403, "The owner cannot be removed")
    revoke_pending_invitations(db, organization_id, db.get(User, member_id).email, utcnow())
    db.delete(member)
    audit(db, user, "organization", organization_id, "membership.removed", {
        "user_id": member_id, "role": member.role,
    })
    db.commit()
