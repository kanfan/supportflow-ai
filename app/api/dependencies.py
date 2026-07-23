from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.security import (
    AccessTokenManager,
    InvalidAccessTokenError,
    PasswordManager,
)
from app.identity.models import (
    MembershipRole,
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationStatus,
    User,
    UserStatus,
)


bearer_scheme = HTTPBearer(auto_error=False)


def get_database_session(request: Request) -> Iterator[Session]:
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as session:
        yield session


def get_password_manager(request: Request) -> PasswordManager:
    return request.app.state.password_manager


def get_access_token_manager(request: Request) -> AccessTokenManager:
    return request.app.state.access_token_manager


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    session: Annotated[Session, Depends(get_database_session)],
    token_manager: Annotated[AccessTokenManager, Depends(get_access_token_manager)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized()

    try:
        user_id = token_manager.decode_user_id(credentials.credentials)
    except InvalidAccessTokenError:
        raise unauthorized() from None

    user = session.get(User, user_id)
    if user is None or user.status is not UserStatus.ACTIVE:
        raise unauthorized()
    return user


@dataclass(frozen=True)
class OrganizationContext:
    organization: Organization
    membership: OrganizationMember


def get_organization_context(
    organization_id: Annotated[UUID, Header(alias="X-Organization-ID")],
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_database_session)],
) -> OrganizationContext:
    row = session.execute(
        select(Organization, OrganizationMember)
        .join(
            OrganizationMember,
            OrganizationMember.organization_id == Organization.id,
        )
        .where(
            Organization.id == organization_id,
            Organization.status == OrganizationStatus.ACTIVE,
            OrganizationMember.user_id == current_user.id,
            OrganizationMember.status == MembershipStatus.ACTIVE,
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )
    organization, membership = row
    return OrganizationContext(organization=organization, membership=membership)


def require_roles(
    *allowed_roles: MembershipRole,
) -> Callable[[OrganizationContext], OrganizationContext]:
    allowed = frozenset(allowed_roles)

    def verify_role(
        context: Annotated[OrganizationContext, Depends(get_organization_context)],
    ) -> OrganizationContext:
        if context.membership.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return context

    return verify_role
