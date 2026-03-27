"""
Auth endpoints — local signup/login, JWT-protected /me, Google OAuth2.
"""

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.models.user import User
from app.schemas.auth import Token, UserLogin
from app.schemas.user import UserCreate, UserResponse
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ── Local authentication ────────────────────────

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


# ── Current user ─────────────────────────────────

@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the current authenticated user",
)
def me(current_user: User = Depends(get_current_user)):
    return current_user


# ── Google OAuth2 ────────────────────────────────

@router.get(
    "/google/login",
    summary="Redirect to Google consent screen",
)
def google_login():
    url = auth_service.get_google_auth_url()
    return RedirectResponse(url=url)


@router.get(
    "/google/callback",
    response_model=Token,
    summary="Handle Google OAuth2 callback",
)
async def google_callback(
    code: str = Query(..., description="Authorization code from Google"),
    db: Session = Depends(get_db),
):
    access_token = await auth_service.google_callback(db, code=code)
    return Token(access_token=access_token)
