from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.registration_request import RegistrationProvider, RegistrationRequestStatus


class RegistrationRequestResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    provider: RegistrationProvider
    status: RegistrationRequestStatus
    rejection_reason: str | None = None
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RegistrationRequestListResponse(BaseModel):
    items: list[RegistrationRequestResponse]
    total: int


class RegistrationRequestApprove(BaseModel):
    role_name: str = Field(..., min_length=2, max_length=50)


class RegistrationRequestReject(BaseModel):
    rejection_reason: str = Field(..., min_length=3, max_length=1000)


class SignupRequestResponse(BaseModel):
    message: str
    request: RegistrationRequestResponse


class RegistrationStatusHintResponse(BaseModel):
    status: RegistrationRequestStatus
    message: str
    rejection_reason: str | None = None

