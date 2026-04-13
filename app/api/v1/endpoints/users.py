"""
User management endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_permission
from app.database.session import get_db
from app.models.user import User
from app.schemas.user import (
    UserCreate,
    UserPasswordUpdate,
    UserResponse,
    UserSelfUpdate,
    UserStatusUpdate,
    UserUpdate,
)
from app.services import role_service, user_service

router = APIRouter()


@router.get("/", response_model=list[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("view_users")),
):
    """
    List all users. Requires 'view_users' permission.
    """
    return user_service.list_users(db)


@router.get("/me", response_model=UserResponse)
def get_user_me(current_user: User = Depends(get_current_user)):
    """
    Get current logged-in user information.
    """
    return current_user


@router.patch("/me", response_model=UserResponse)
def update_user_me(
    payload: UserSelfUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update current logged-in user profile fields.
    """
    update_data = payload.model_dump(exclude_unset=True)
    return user_service.update_user(
        db,
        current_user,
        actor_user_id=current_user.id,
        full_name=update_data.get("full_name"),
        avatar_url=update_data.get("avatar_url"),
    )


@router.patch("/me/password", response_model=UserResponse)
def update_my_password(
    payload: UserPasswordUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Change the current user's password.
    """
    return user_service.change_user_password(
        db,
        current_user,
        actor_user_id=current_user.id,
        current_password=payload.current_password,
        new_password=payload.new_password,
        require_current_password=True,
    )


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("view_users")),
):
    """
    Get user by ID. Requires 'view_users' permission.
    """
    user = user_service.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("manage_users")),
):
    """
    Update user fields. Requires 'manage_users' permission.
    """
    user = user_service.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    update_data = payload.model_dump(exclude_unset=True)
    email = update_data.get("email")
    if email:
        existing = user_service.get_user_by_email(db, email)
        if existing and str(existing.id) != str(user.id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists",
            )

    updated_user = user_service.update_user(
        db,
        user,
        actor_user_id=current_user.id,
        email=email,
        full_name=update_data.get("full_name"),
    )

    password = update_data.get("password")
    if password:
        updated_user = user_service.change_user_password(
            db,
            updated_user,
            actor_user_id=current_user.id,
            new_password=password,
            require_current_password=False,
        )

    role_name = update_data.get("role_name")
    if role_name:
        updated_user = role_service.replace_user_roles(
            db,
            user_id=updated_user.id,
            role_name=role_name,
            actor_user_id=current_user.id,
        )

    return updated_user


@router.patch("/{user_id}/status", response_model=UserResponse)
def set_user_status(
    user_id: str,
    payload: UserStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("manage_users")),
):
    """
    Activate or deactivate a user account. Requires 'manage_users' permission.
    """
    user = user_service.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if str(current_user.id) == str(user.id) and payload.is_active is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account.",
        )

    return user_service.set_user_active_status(
        db,
        user,
        actor_user_id=current_user.id,
        is_active=payload.is_active,
    )


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    user_in: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("manage_users")),
):
    """
    Create a new user. Requires 'manage_users' permission.
    """
    user = user_service.get_user_by_email(db, user_in.email)
    if user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists",
        )
    return user_service.create_user(
        db,
        email=user_in.email,
        full_name=user_in.full_name,
        password=user_in.password,
    )
