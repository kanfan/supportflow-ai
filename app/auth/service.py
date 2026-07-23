from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.schemas import RegisterRequest
from app.auth.security import PasswordManager
from app.identity.models import (
    MembershipRole,
    Organization,
    OrganizationMember,
    User,
    UserStatus,
)


class RegistrationConflictError(ValueError):
    """Raised when registration collides with an existing identity or tenant."""


class InvalidCredentialsError(ValueError):
    """Raised for every failed login without disclosing the reason."""


@dataclass(frozen=True)
class RegistrationResult:
    user: User
    organization: Organization
    membership: OrganizationMember


class AuthenticationService:
    def __init__(self, session: Session, password_manager: PasswordManager) -> None:
        self._session = session
        self._password_manager = password_manager

    def register(self, request: RegisterRequest) -> RegistrationResult:
        user = User(
            email=str(request.email).strip().lower(),
            password_hash=self._password_manager.hash(
                request.password.get_secret_value()
            ),
        )
        organization = Organization(
            name=request.organization_name,
            slug=request.organization_slug,
        )
        membership = OrganizationMember(
            user=user,
            organization=organization,
            role=MembershipRole.ADMIN,
        )
        self._session.add(membership)

        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise RegistrationConflictError from exc

        return RegistrationResult(
            user=user,
            organization=organization,
            membership=membership,
        )

    def authenticate(self, email: str, password: str) -> User:
        normalized_email = email.strip().lower()
        user = self._session.scalar(select(User).where(User.email == normalized_email))

        if user is None:
            self._password_manager.verify_dummy(password)
            raise InvalidCredentialsError

        if not self._password_manager.verify(password, user.password_hash):
            raise InvalidCredentialsError

        if user.status is not UserStatus.ACTIVE:
            raise InvalidCredentialsError

        return user
