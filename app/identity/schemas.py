from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator

from app.identity.models import MembershipRole, MembershipStatus


class OrganizationMemberCreateRequest(BaseModel):
    email: EmailStr
    role: Literal[MembershipRole.AGENT]

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class OrganizationMemberResponse(BaseModel):
    user_id: UUID
    email: str
    role: MembershipRole
    status: MembershipStatus
    created_at: datetime
    updated_at: datetime
