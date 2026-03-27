"""
Authentication service — local signup/login and Google OAuth2 flow.
"""

from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token, verify_password
from app.models.user import User
from app.services.user_service import create_user, get_user_by_email
from app.services import audit_service

# ── Google OAuth constants ───────────────────────
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


# ─────────────────────────────────────────────────
# Local authentication
# ─────────────────────────────────────────────────

def signup(db: Session, *, email: str, password: str, full_name: str) -> User:
    """
    Register a new local user.

    Raises 409 if the email is already taken.
    """
    existing = get_user_by_email(db, email)
    if existing:
        audit_service.log_auth_event(db, "signup", "failed", message="Email already exists")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )
    user = create_user(db, email=email, full_name=full_name, password=password)
    audit_service.log_auth_event(db, "signup", "success", user_id=user.id)
    return user


def authenticate(db: Session, *, email: str, password: str) -> str:
    """
    Authenticate a local user and return a JWT access token.

    Raises 401 on bad credentials.
    """
    user = get_user_by_email(db, email)
    if not user or not user.hashed_password:
        audit_service.log_auth_event(db, "login", "failed", message=f"Invalid email: {email}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    if not verify_password(password, user.hashed_password):
        audit_service.log_auth_event(db, "login", "failed", user_id=user.id, message="Invalid password")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    if not user.is_active:
        audit_service.log_auth_event(db, "login", "denied", user_id=user.id, message="Account deactivated")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated.",
        )
    audit_service.log_auth_event(db, "login", "success", user_id=user.id)
    return create_access_token(user.id)


# ─────────────────────────────────────────────────
# Google OAuth2
# ─────────────────────────────────────────────────

def get_google_auth_url() -> str:
    """Build the Google OAuth2 consent-screen URL."""
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def google_callback(db: Session, *, code: str) -> str:
    """
    Handle the Google OAuth2 callback.

    1. Exchange *code* for an access token.
    2. Fetch user info from Google.
    3. Find or create the local User.
    4. Return a JWT access token.
    """
    # Step 1 — Exchange authorization code for tokens
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )

    if token_response.status_code != 200:
        audit_service.log_auth_event(db, "google_login", "failed", message="Failed to exchange Google authorization code.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to exchange Google authorization code.",
        )

    token_data = token_response.json()
    google_access_token = token_data.get("access_token")
    if not google_access_token:
        audit_service.log_auth_event(db, "google_login", "failed", message="Google did not return an access token.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google did not return an access token.",
        )

    # Step 2 — Fetch user info
    async with httpx.AsyncClient() as client:
        userinfo_response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {google_access_token}"},
        )

    if userinfo_response.status_code != 200:
        audit_service.log_auth_event(db, "google_login", "failed", message="Failed to fetch user info from Google.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to fetch user info from Google.",
        )

    google_user = userinfo_response.json()
    email: str = google_user["email"]
    full_name: str = google_user.get("name", email.split("@")[0])
    google_id: str = google_user["id"]
    avatar: str | None = google_user.get("picture")

    # Step 3 — Find or create user
    user = get_user_by_email(db, email)
    if not user:
        user = create_user(
            db,
            email=email,
            full_name=full_name,
            provider="google",
            provider_id=google_id,
            avatar_url=avatar,
            is_verified=True,
        )
    else:
        # Update provider info if user previously registered locally
        if user.provider == "local":
            user.provider = "google"
            user.provider_id = google_id
        if avatar and not user.avatar_url:
            user.avatar_url = avatar
        user.is_verified = True
        db.commit()
        db.refresh(user)

    # Step 4 — Issue JWT
    audit_service.log_auth_event(db, "google_login", "success", user_id=user.id)
    return create_access_token(user.id)
