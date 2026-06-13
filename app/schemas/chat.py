from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ChatMessageBase(BaseModel):
    role: str
    content: str
    intent: Optional[str] = None
    handler: Optional[str] = None
    data: Optional[Any] = None
    suggested_actions: Optional[List[str]] = None
    metadata_json: Optional[Dict[str, Any]] = None


class ChatMessageCreate(ChatMessageBase):
    pass


class ChatMessageResponse(ChatMessageBase):
    id: UUID
    session_id: UUID
    created_at: datetime

    class Config:
        from_attributes = True


class ChatSessionBase(BaseModel):
    title: str


class ChatSessionCreate(ChatSessionBase):
    pass


class ChatSessionUpdate(ChatSessionBase):
    pass


class ChatSessionResponse(ChatSessionBase):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ChatSessionDetailResponse(ChatSessionResponse):
    messages: List[ChatMessageResponse] = []
