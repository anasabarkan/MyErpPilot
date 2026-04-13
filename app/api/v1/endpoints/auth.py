"""
Auth endpoints: local signup/login, JWT-protected /me, Google OAuth2.
"""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.models.user import User
from app.schemas.auth import CurrentUserResponse, FirstLoginPasswordChange, Token, UserLogin
from app.schemas.user import UserCreate, UserResponse
from app.services import auth_service
from app.services.role_service import get_user_permissions_list

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/signup",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new local user",
)
def signup(body: UserCreate, db: Session = Depends(get_db)):
    user = auth_service.signup(
        db, email=body.email, password=body.password, full_name=body.full_name
    )
    return user


@router.post(
    "/login",
    response_model=Token,
    summary="Login with email and password",
)
def login(body: UserLogin, db: Session = Depends(get_db)):
    access_token = auth_service.authenticate(
        db, email=body.email, password=body.password
    )
    return Token(access_token=access_token)


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    summary="Get the current authenticated user",
)
def me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return CurrentUserResponse(
        **UserResponse.model_validate(current_user).model_dump(),
        permissions=get_user_permissions_list(db, user_id=current_user.id),
    )


@router.post(
    "/change-password-first-login",
    response_model=UserResponse,
    summary="Change password on first login and verify the account",
)
def change_password_first_login(
    body: FirstLoginPasswordChange,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = auth_service.change_password_first_login(
        db,
        user=current_user,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    return user


@router.get(
    "/google/login",
    summary="Redirect to Google consent screen",
)
def google_login():
    url = auth_service.get_google_auth_url()
    return RedirectResponse(url=url)


@router.get(
    "/google/callback",
    summary="Handle Google OAuth2 callback and redirect to frontend",
)
async def google_callback(
    code: str = Query(..., description="Authorization code from Google"),
    db: Session = Depends(get_db),
):
    try:
        access_token = await auth_service.google_callback(db, code=code)
        query = urlencode({"token": access_token})
    except HTTPException as exc:
        query = urlencode({"error": str(exc.detail)})

    redirect_url = f"{settings.FRONTEND_GOOGLE_CALLBACK_URL}?{query}"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
