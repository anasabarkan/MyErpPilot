from typing import Sequence
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_permission
from app.database.session import get_db
from app.models.user import User
from app.repositories import role_repository
from app.schemas.role import AssignRoleToUser, RoleCreate, RoleResponse
from app.services import role_service

router = APIRouter()


@router.post(
    "/",
    response_model=RoleResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("manage_roles"))],
)
def create_role(
    role_in: RoleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new role.
    Requires permission: 'manage_roles'.
    """
    return role_repository.create_role(db, name=role_in.name, description=role_in.description)


@router.get(
    "/",
    response_model=list[RoleResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("manage_users"))],
)
def get_roles(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all available roles.
    Requires permission: 'manage_users'.
    """
    return role_service.list_roles(db)


@router.post(
    "/assign",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("manage_users"))],
)
def assign_role(
    user_id: UUID,
    payload: AssignRoleToUser,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Assign a role to a user.
    Requires permission: 'manage_users'.
    """
    role_service.assign_role_to_user(db, user_id=user_id, role_name=payload.role_name)
    return {"message": f"Role '{payload.role_name}' assigned to user {user_id}"}


@router.get(
    "/users/{user_id}/roles",
    response_model=list[RoleResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("manage_users"))],
)
def get_usr_roles(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get roles belonging to a user.
    Requires permission: 'manage_users'.
    """
    return role_service.get_user_roles(db, user_id=user_id)
