from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID
import hmac

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_database_session, get_password_manager
from app.auth.schemas import LoginRequest, OrganizationSlug
from app.auth.security import PasswordManager
from app.auth.service import AuthenticationService, InvalidCredentialsError
from app.identity.models import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationStatus,
    User,
    UserStatus,
)
from app.tickets.models import TicketSourceType, TicketStatus
from app.tickets.schemas import TicketMessageCreateRequest, TicketStatusUpdateRequest
from app.tickets.service import (
    InvalidTicketTransitionError,
    TicketNotFoundError,
    TicketPersistenceError,
    TicketService,
)
from app.ui.session import (
    BrowserSession,
    BrowserSessionStore,
    BrowserSessionStoreUnavailableError,
    UI_SESSION_COOKIE,
)


router = APIRouter(prefix="/ui", tags=["agent-ui"])
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


class BrowserLoginRequest(LoginRequest):
    organization_slug: OrganizationSlug


@dataclass(frozen=True)
class BrowserOrganizationContext:
    session: BrowserSession
    user: User
    organization: Organization
    membership: OrganizationMember


def get_browser_session_store(request: Request) -> BrowserSessionStore:
    return request.app.state.browser_session_store


def cookie_secure(request: Request) -> bool:
    return request.app.state.settings.environment in {"staging", "production"}


def set_session_cookie(
    response: Response,
    request: Request,
    cookie_value: str,
) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        key=UI_SESSION_COOKIE,
        value=cookie_value,
        max_age=settings.ui_session_ttl_minutes * 60,
        httponly=True,
        secure=cookie_secure(request),
        samesite="lax",
        path="/",
    )


def resolve_browser_context(
    request: Request,
    session: Session,
) -> BrowserOrganizationContext | None:
    store = get_browser_session_store(request)
    browser_session = store.resolve(request.cookies.get(UI_SESSION_COOKIE))
    if browser_session is None or not browser_session.is_authenticated:
        return None

    row = session.execute(
        select(User, Organization, OrganizationMember)
        .join(
            OrganizationMember,
            OrganizationMember.user_id == User.id,
        )
        .join(
            Organization,
            Organization.id == OrganizationMember.organization_id,
        )
        .where(
            User.id == browser_session.user_id,
            User.status == UserStatus.ACTIVE,
            Organization.id == browser_session.organization_id,
            Organization.status == OrganizationStatus.ACTIVE,
            OrganizationMember.status == MembershipStatus.ACTIVE,
        )
    ).one_or_none()
    if row is None:
        store.invalidate(request.cookies.get(UI_SESSION_COOKIE))
        return None
    user, organization, membership = row
    return BrowserOrganizationContext(
        session=browser_session,
        user=user,
        organization=organization,
        membership=membership,
    )


def login_redirect() -> RedirectResponse:
    return RedirectResponse("/ui/login", status_code=status.HTTP_303_SEE_OTHER)


def render_error(
    request: Request,
    *,
    message: str,
    status_code: int,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "error.html",
        {"message": message},
        status_code=status_code,
    )


async def browser_session_unavailable_handler(
    request: Request,
    exc: Exception,
) -> HTMLResponse:
    if not isinstance(exc, BrowserSessionStoreUnavailableError):
        raise exc
    return render_error(
        request,
        message="Oturum hizmeti geçici olarak kullanılamıyor. Lütfen tekrar deneyin.",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def valid_csrf(context: BrowserOrganizationContext, supplied_token: object) -> bool:
    return (
        isinstance(supplied_token, str)
        and bool(supplied_token)
        and hmac.compare_digest(supplied_token, context.session.csrf_token)
    )


def form_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def ticket_list_url(
    *,
    limit: int,
    offset: int,
    ticket_status: TicketStatus | None,
    source_type: TicketSourceType | None,
    customer_id: UUID | None,
) -> str:
    query: dict[str, str | int] = {"limit": limit, "offset": max(0, offset)}
    if ticket_status is not None:
        query["status"] = ticket_status.value
    if source_type is not None:
        query["source_type"] = source_type.value
    if customer_id is not None:
        query["customer_id"] = str(customer_id)
    return f"/ui/tickets?{urlencode(query)}"


@router.get("", include_in_schema=False)
def ui_root() -> RedirectResponse:
    return RedirectResponse("/ui/tickets", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(
    request: Request,
    session: Annotated[Session, Depends(get_database_session)],
) -> Response:
    existing = resolve_browser_context(request, session)
    if existing is not None:
        return RedirectResponse(
            "/ui/tickets",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    store = get_browser_session_store(request)
    cookie_value, anonymous_session = store.create_anonymous(
        request.cookies.get(UI_SESSION_COOKIE)
    )
    response = templates.TemplateResponse(
        request,
        "login.html",
        {"csrf_token": anonymous_session.csrf_token, "error": None},
    )
    set_session_cookie(response, request, cookie_value)
    return response


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_submit(
    request: Request,
    session: Annotated[Session, Depends(get_database_session)],
    password_manager: Annotated[PasswordManager, Depends(get_password_manager)],
) -> Response:
    store = get_browser_session_store(request)
    cookie_value = request.cookies.get(UI_SESSION_COOKIE)
    anonymous_session = store.resolve(cookie_value)
    form = await request.form()
    supplied_csrf = form.get("csrf_token")
    if (
        anonymous_session is None
        or anonymous_session.is_authenticated
        or not isinstance(supplied_csrf, str)
        or not hmac.compare_digest(supplied_csrf, anonymous_session.csrf_token)
    ):
        return render_error(
            request,
            message="Oturum doğrulaması başarısız. Giriş sayfasını yenileyin.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        payload = BrowserLoginRequest.model_validate(
            {
                "email": form_text(form.get("email")),
                "password": form_text(form.get("password")),
                "organization_slug": form_text(form.get("organization_slug")),
            }
        )
        user = AuthenticationService(session, password_manager).authenticate(
            str(payload.email),
            payload.password.get_secret_value(),
        )
    except (InvalidCredentialsError, ValidationError):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "csrf_token": anonymous_session.csrf_token,
                "error": "E-posta, parola veya organizasyon bilgisi geçersiz.",
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    organization = session.scalar(
        select(Organization)
        .join(
            OrganizationMember,
            OrganizationMember.organization_id == Organization.id,
        )
        .where(
            Organization.slug == payload.organization_slug,
            Organization.status == OrganizationStatus.ACTIVE,
            OrganizationMember.user_id == user.id,
            OrganizationMember.status == MembershipStatus.ACTIVE,
        )
    )
    if organization is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "csrf_token": anonymous_session.csrf_token,
                "error": "E-posta, parola veya organizasyon bilgisi geçersiz.",
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    authenticated_cookie, _authenticated_session = store.authenticate(
        previous_cookie=cookie_value,
        user_id=user.id,
        organization_id=organization.id,
    )
    response = RedirectResponse(
        "/ui/tickets",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_session_cookie(response, request, authenticated_cookie)
    return response


@router.post("/logout", include_in_schema=False)
async def logout(
    request: Request,
    session: Annotated[Session, Depends(get_database_session)],
) -> Response:
    context = resolve_browser_context(request, session)
    if context is None:
        return login_redirect()
    form = await request.form()
    if not valid_csrf(context, form.get("csrf_token")):
        return render_error(
            request,
            message="Oturum doğrulaması başarısız.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    get_browser_session_store(request).invalidate(
        request.cookies.get(UI_SESSION_COOKIE)
    )
    response = login_redirect()
    response.delete_cookie(UI_SESSION_COOKIE, path="/")
    return response


@router.get("/tickets", response_class=HTMLResponse, include_in_schema=False)
def ticket_list_page(
    request: Request,
    session: Annotated[Session, Depends(get_database_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    ticket_status: Annotated[
        TicketStatus | None,
        Query(alias="status"),
    ] = None,
    source_type: TicketSourceType | None = None,
    customer_id: UUID | None = None,
) -> Response:
    context = resolve_browser_context(request, session)
    if context is None:
        return login_redirect()
    page = TicketService(session, context.organization.id).list_tickets(
        limit=limit,
        offset=offset,
        ticket_status=ticket_status,
        source_type=source_type,
        customer_id=customer_id,
    )
    previous_url = None
    if offset > 0:
        previous_url = ticket_list_url(
            limit=limit,
            offset=offset - limit,
            ticket_status=ticket_status,
            source_type=source_type,
            customer_id=customer_id,
        )
    next_url = None
    if offset + limit < page.total:
        next_url = ticket_list_url(
            limit=limit,
            offset=offset + limit,
            ticket_status=ticket_status,
            source_type=source_type,
            customer_id=customer_id,
        )
    return templates.TemplateResponse(
        request,
        "tickets.html",
        {
            "context": context,
            "tickets": page.items,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": page.total,
            },
            "filters": {
                "status": ticket_status.value if ticket_status else "",
                "source_type": source_type.value if source_type else "",
                "customer_id": str(customer_id) if customer_id else "",
            },
            "ticket_statuses": list(TicketStatus),
            "ticket_source_types": list(TicketSourceType),
            "previous_url": previous_url,
            "next_url": next_url,
        },
    )


@router.get(
    "/tickets/{ticket_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def ticket_detail_page(
    request: Request,
    ticket_id: UUID,
    session: Annotated[Session, Depends(get_database_session)],
) -> Response:
    context = resolve_browser_context(request, session)
    if context is None:
        return login_redirect()
    try:
        detail = TicketService(
            session,
            context.organization.id,
        ).get_ticket_detail(ticket_id)
    except TicketNotFoundError:
        return render_error(
            request,
            message="Ticket bulunamadı.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return templates.TemplateResponse(
        request,
        "ticket_detail.html",
        {
            "context": context,
            "ticket": detail.ticket,
            "messages": detail.messages,
            "next_status": NEXT_STATUS.get(detail.ticket.status),
            "error": None,
        },
    )


NEXT_STATUS = {
    TicketStatus.OPEN: TicketStatus.PROCESSING,
    TicketStatus.PROCESSING: TicketStatus.WAITING_FOR_AGENT,
    TicketStatus.WAITING_FOR_AGENT: TicketStatus.RESOLVED,
    TicketStatus.RESOLVED: TicketStatus.CLOSED,
}


@router.post("/tickets/{ticket_id}/messages", include_in_schema=False)
async def ticket_message_submit(
    request: Request,
    ticket_id: UUID,
    session: Annotated[Session, Depends(get_database_session)],
) -> Response:
    context = resolve_browser_context(request, session)
    if context is None:
        return login_redirect()
    form = await request.form()
    if not valid_csrf(context, form.get("csrf_token")):
        return render_error(
            request,
            message="CSRF doğrulaması başarısız.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    try:
        payload = TicketMessageCreateRequest.model_validate(
            {"body": form_text(form.get("body"))}
        )
        TicketService(session, context.organization.id).append_agent_message(
            ticket_id=ticket_id,
            body=payload.body,
            current_user_id=context.user.id,
        )
    except ValidationError:
        return render_error(
            request,
            message="Mesaj boş olamaz.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except TicketNotFoundError:
        return render_error(
            request,
            message="Ticket bulunamadı.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except TicketPersistenceError:
        return render_error(
            request,
            message="Mesaj kaydedilemedi.",
            status_code=status.HTTP_409_CONFLICT,
        )
    return RedirectResponse(
        f"/ui/tickets/{ticket_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/tickets/{ticket_id}/status", include_in_schema=False)
async def ticket_status_submit(
    request: Request,
    ticket_id: UUID,
    session: Annotated[Session, Depends(get_database_session)],
) -> Response:
    context = resolve_browser_context(request, session)
    if context is None:
        return login_redirect()
    form = await request.form()
    if not valid_csrf(context, form.get("csrf_token")):
        return render_error(
            request,
            message="CSRF doğrulaması başarısız.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    try:
        payload = TicketStatusUpdateRequest.model_validate(
            {"status": form_text(form.get("status"))}
        )
        TicketService(session, context.organization.id).transition_ticket(
            ticket_id=ticket_id,
            next_status=payload.status,
            current_user_id=context.user.id,
        )
    except ValidationError:
        return render_error(
            request,
            message="Geçersiz ticket durumu.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except TicketNotFoundError:
        return render_error(
            request,
            message="Ticket bulunamadı.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except InvalidTicketTransitionError:
        return render_error(
            request,
            message="Bu status geçişine izin verilmiyor.",
            status_code=status.HTTP_409_CONFLICT,
        )
    except TicketPersistenceError:
        return render_error(
            request,
            message="Ticket durumu güncellenemedi.",
            status_code=status.HTTP_409_CONFLICT,
        )
    return RedirectResponse(
        f"/ui/tickets/{ticket_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
