from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_access_token_manager,
    get_current_user,
    get_database_session,
    get_password_manager,
)
from app.auth.schemas import (
    LoginRequest,
    OrganizationResponse,
    RegisterRequest,
    RegistrationResponse,
    TokenResponse,
    UserResponse,
)
from app.auth.security import AccessTokenManager, PasswordManager
from app.auth.service import (
    AuthenticationService,
    InvalidCredentialsError,
    RegistrationConflictError,
)
from app.identity.models import User


router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


@router.post(
    "/register",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    request: RegisterRequest,
    session: Annotated[Session, Depends(get_database_session)],
    password_manager: Annotated[PasswordManager, Depends(get_password_manager)],
) -> RegistrationResponse:
    service = AuthenticationService(session, password_manager)
    try:
        result = service.register(request)
    except RegistrationConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email or organization slug already exists",
        ) from None

    return RegistrationResponse(
        user=UserResponse.model_validate(result.user),
        organization=OrganizationResponse.model_validate(result.organization),
        role=result.membership.role,
        membership_status=result.membership.status,
    )


@router.post("/login", response_model=TokenResponse)
def login(
    request: LoginRequest,
    session: Annotated[Session, Depends(get_database_session)],
    password_manager: Annotated[PasswordManager, Depends(get_password_manager)],
    token_manager: Annotated[AccessTokenManager, Depends(get_access_token_manager)],
) -> TokenResponse:
    service = AuthenticationService(session, password_manager)
    try:
        user = service.authenticate(
            str(request.email),
            request.password.get_secret_value(),
        )
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    token = token_manager.issue(user.id)
    return TokenResponse(access_token=token.value, expires_in=token.expires_in)


@router.get("/me", response_model=UserResponse)
def read_current_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    return current_user
