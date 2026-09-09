from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import AppError
from app.core.rate_limit import rate_limit_auth
from app.core.security import (
    create_access_token,
    dummy_password_verify,
    get_current_user,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_auth)],
)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing is not None:
        raise AppError("Email already registered", status_code=status.HTTP_409_CONFLICT)

    user = User(
        name=payload.name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        risk_profile=payload.risk_profile,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(subject=str(user.id))
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(rate_limit_auth)])
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.email == payload.email).first()

    # A bcrypt verify is always performed, even when the email is unknown.
    # `user is None or not verify_password(...)` short-circuits, so a missing
    # account answered in about a millisecond while a real one took ~214ms —
    # a 200x timing gap that turns this endpoint into an account-existence
    # oracle. Both paths now do the same work and return the same message.
    if user is None:
        dummy_password_verify(payload.password)
        raise AppError("Invalid email or password", status_code=status.HTTP_401_UNAUTHORIZED)
    if not verify_password(payload.password, user.password_hash):
        raise AppError("Invalid email or password", status_code=status.HTTP_401_UNAUTHORIZED)

    token = create_access_token(subject=str(user.id))
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
