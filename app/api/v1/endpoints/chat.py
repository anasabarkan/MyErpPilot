from typing import Any, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_permission
from app.database.session import get_db
from app.models.chat import ChatSession
from app.models.user import User
from app.schemas.chat import (
    ChatSessionCreate,
    ChatSessionDetailResponse,
    ChatSessionResponse,
    ChatSessionUpdate,
)

router = APIRouter()


@router.post(
    "",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("query_data"))],
)
def create_chat_session(
    *,
    db: Session = Depends(get_db),
    session_in: ChatSessionCreate,
    current_user: User = Depends(get_current_user),
) -> Any:
    """Create a new chat session."""
    session = ChatSession(
        user_id=current_user.id,
        title=session_in.title,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get(
    "",
    response_model=List[ChatSessionResponse],
    dependencies=[Depends(require_permission("query_data"))],
)
def list_chat_sessions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Retrieve all chat sessions for the current user."""
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )
    return sessions


@router.get(
    "/{session_id}",
    response_model=ChatSessionDetailResponse,
    dependencies=[Depends(require_permission("query_data"))],
)
def get_chat_session(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Get a specific chat session and its messages."""
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found.",
        )
    return session


@router.put(
    "/{session_id}",
    response_model=ChatSessionResponse,
    dependencies=[Depends(require_permission("query_data"))],
)
def update_chat_session(
    session_id: UUID,
    session_in: ChatSessionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Update a chat session (e.g., rename title)."""
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found.",
        )

    session.title = session_in.title
    db.commit()
    db.refresh(session)
    return session


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("query_data"))],
)
def delete_chat_session(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete a chat session."""
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found.",
        )
    db.delete(session)
    db.commit()
