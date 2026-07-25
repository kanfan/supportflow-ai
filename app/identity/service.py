from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.identity.models import (
    MembershipRole,
    MembershipStatus,
    OrganizationMember,
    User,
    UserStatus,
)


class MembershipConflictError(ValueError):
    """Raised when the requested organization membership already exists."""


class MembershipUserNotFoundError(ValueError):
    """Raised when the target email does not identify an active user."""


@dataclass(frozen=True)
class OrganizationMemberPage:
    items: list[OrganizationMember]
    total: int


class OrganizationMembershipService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_members(
        self,
        *,
        organization_id: UUID,
        limit: int,
        offset: int,
    ) -> OrganizationMemberPage:
        total = self._session.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(OrganizationMember.organization_id == organization_id)
        )
        items = list(
            self._session.scalars(
                select(OrganizationMember)
                .options(joinedload(OrganizationMember.user))
                .where(OrganizationMember.organization_id == organization_id)
                .order_by(
                    OrganizationMember.created_at.desc(),
                    OrganizationMember.user_id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        )
        return OrganizationMemberPage(items=items, total=total or 0)

    def add_agent(
        self,
        *,
        organization_id: UUID,
        normalized_email: str,
    ) -> OrganizationMember:
        user = self._session.scalar(
            select(User).where(
                User.email == normalized_email,
                User.status == UserStatus.ACTIVE,
            )
        )
        if user is None:
            raise MembershipUserNotFoundError

        existing_membership = self._session.get(
            OrganizationMember,
            (organization_id, user.id),
        )
        if existing_membership is not None:
            raise MembershipConflictError

        membership = OrganizationMember(
            organization_id=organization_id,
            user=user,
            role=MembershipRole.AGENT,
            status=MembershipStatus.ACTIVE,
        )
        self._session.add(membership)

        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise MembershipConflictError from exc

        return membership
