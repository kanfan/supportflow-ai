from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.tickets.models import (
    MessageAuthorType,
    TicketSourceType,
    TicketStatus,
)


NonEmptyBody = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50_000),
]
TicketSubject = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
]


class InitialMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: NonEmptyBody
    author_type: Literal["customer", "agent"] = "agent"


class TicketCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: TicketSubject
    customer_id: UUID | None = None
    source_type: TicketSourceType = TicketSourceType.MANUAL
    external_id: str | None = Field(default=None, max_length=255)
    initial_message: InitialMessageRequest

    @model_validator(mode="after")
    def require_customer_for_customer_message(self) -> "TicketCreateRequest":
        if (
            self.initial_message.author_type == MessageAuthorType.CUSTOMER
            and self.customer_id is None
        ):
            raise ValueError(
                "customer_id is required for a customer-authored initial message"
            )
        return self


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    customer_id: UUID | None
    source_type: TicketSourceType
    external_id: str | None
    subject: str
    status: TicketStatus
    created_at: datetime
    updated_at: datetime


class TicketMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    ticket_id: UUID
    author_type: MessageAuthorType
    author_user_id: UUID | None
    author_customer_id: UUID | None
    body: str
    created_at: datetime


class TicketCreateResponse(TicketResponse):
    initial_message: TicketMessageResponse


class PaginationResponse(BaseModel):
    limit: int
    offset: int
    total: int


class TicketListResponse(BaseModel):
    items: list[TicketResponse]
    pagination: PaginationResponse
