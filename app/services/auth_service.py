"""
Authentication service: local signup/login and Google OAuth2 flow.
"""

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.services import audit_service, registration_request_service
from app.services.user_service import get_user_by_email

# Google OAuth constants
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


@dataclass
class GoogleCallbackResult:
    outcome: Literal["token", "registration_pending", "registration_rejected"]
    token: str | None = None
    message: str | None = None
    rejection_reason: str | None = None


def signup(db: Session, *, email: str, password: str, full_name: str):
    """
    Create a local registration request instead of creating a user directly.
    """
    request = registration_request_service.create_local_registration_request(
        db,
        email=email,
        password=password,
        full_name=full_name,
    )
    return request


def authenticate(db: Session, *, email: str, password: str) -> str:
    """
    Authenticate a local user and return a JWT access token.

    Returns explicit registration status if the account is pending/rejected.
    """
    normalized_email = email.strip().lower()
    user = get_user_by_email(db, normalized_email)
    if not user or not user.hashed_password:
        status_hint = registration_request_service.get_registration_status_hint_for_login(
            db,
            email=normalized_email,
        )
        if status_hint:
            audit_service.log_auth_event(
                db,
                "login",
                "denied",
                message=f"Login blocked: {status_hint.status.value} registration ({normalized_email}).",
            )
            if status_hint.rejection_reason:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"{status_hint.message} Reason: {status_hint.rejection_reason}",
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=status_hint.message,
            )

        audit_service.log_auth_event(db, "login", "failed", message=f"Invalid email: {normalized_email}")
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


def change_password_first_login(
    db: Session,
    *,
    user: User,
    current_password: str,
    new_password: str,
) -> User:
    """
    Keep legacy flow for admin-created local users with is_verified=False.
    """
    if user.provider != "local":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="First-login password change is only supported for local accounts.",
        )

    if user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account is already verified.",
        )

    if not user.hashed_password or not verify_password(current_password, user.hashed_password):
        audit_service.log_auth_event(
            db,
            "first_login_password_change",
            "failed",
            user_id=user.id,
            message="Invalid current password.",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is invalid.",
        )

    if current_password == new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from current password.",
        )

    user.hashed_password = hash_password(new_password)
    user.is_verified = True
    db.commit()
    db.refresh(user)

    audit_service.log_auth_event(
        db,
        "first_login_password_change",
        "success",
        user_id=user.id,
        message="Password changed and account verified.",
    )

    return user


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


async def google_callback(db: Session, *, code: str) -> GoogleCallbackResult:
    """
    Handle the Google OAuth2 callback.

    Existing users get a token immediately.
    New users create a pending registration request.
    """
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
    email: str = google_user["email"].strip().lower()
    full_name: str = google_user.get("name", email.split("@")[0])
    google_id: str = google_user["id"]
    avatar: str | None = google_user.get("picture")

    user = get_user_by_email(db, email)
    if user:
        if user.provider == "local":
            user.provider = "google"
            user.provider_id = google_id
        if avatar and not user.avatar_url:
            user.avatar_url = avatar
        db.commit()
        db.refresh(user)

        if not user.is_active:
            audit_service.log_auth_event(db, "google_login", "denied", user_id=user.id, message="Account deactivated")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is deactivated.",
            )

        audit_service.log_auth_event(db, "google_login", "success", user_id=user.id)
        return GoogleCallbackResult(
            outcome="token",
            token=create_access_token(user.id),
        )

    request = registration_request_service.create_google_registration_request(
        db,
        email=email,
        full_name=full_name,
        provider_id=google_id,
        avatar_url=avatar,
    )
    audit_service.log_auth_event(
        db,
        "google_login",
        "denied",
        message=f"Google registration pending approval for {request.email}.",
    )
    return GoogleCallbackResult(
        outcome="registration_pending",
        message=registration_request_service.PENDING_APPROVAL_MESSAGE,
    )
