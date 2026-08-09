"""Authentication endpoints: register, login, current user."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..security import (
    MIN_PASSWORD_LENGTH,
    create_access_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

CUSTOMER_ROLE = "customer"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=72)
    name: str = Field(min_length=1, max_length=100)


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str

    @classmethod
    def from_model(cls, user: User) -> "UserOut":
        return cls(
            id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
        )


class RegisterResponse(BaseModel):
    user: UserOut


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> RegisterResponse:
    """Register a new customer account. Agents/admins are created by admins."""
    email = body.email.lower()

    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "conflict", "message": "该邮箱已被注册"},
        )

    user = User(
        email=email,
        password_hash=hash_password(body.password),
        name=body.name.strip(),
        # Public registration can only ever create customer accounts; the role
        # is never taken from the request body.
        role=CUSTOMER_ROLE,
    )
    try:
        db.add(user)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "internal_error", "message": "注册失败，请稍后重试"},
        )
    db.refresh(user)
    return RegisterResponse(user=UserOut.from_model(user))


@router.post("/login", response_model=LoginResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> LoginResponse:
    """Log in with email (``username`` field) and password.

    Uses the OAuth2 password flow so Swagger's "Authorize" button works, and
    returns a JWT access token plus the basic user profile.
    """
    email = form_data.username.strip().lower()
    user = db.scalar(select(User).where(User.email == email))

    # Uniform 401 whether the email does not exist or the password is wrong,
    # so the endpoint does not leak which one failed.
    if user is None or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "邮箱或密码错误"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "账号已被停用"},
        )

    token = create_access_token(user.id, user.role)
    from ..config import ACCESS_TOKEN_EXPIRE_MINUTES

    return LoginResponse(
        access_token=token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserOut.from_model(user),
    )


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> UserOut:
    """Return the authenticated user's own profile."""
    return UserOut.from_model(current_user)
