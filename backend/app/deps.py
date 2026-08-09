"""FastAPI dependencies for authentication and role-based access control."""

from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .security import InvalidTokenError, decode_access_token

# ``auto_error=False`` lets us return a uniform 401 with a JSON body instead of
# FastAPI's plain "Not authenticated" response.
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the authenticated user from the Bearer token.

    Returns 401 when the token is missing, invalid or expired, or when the
    user no longer exists or has been deactivated.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "未提供认证凭据"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(credentials.credentials)
        user_id = UUID(payload["sub"])
    except (InvalidTokenError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "认证凭据无效或已过期"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "用户不存在或已被停用"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_roles(*roles: str):
    """Return a dependency that allows only the given roles.

    Usage: ``dependencies=[Depends(require_roles("agent", "admin"))]``
    """

    def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "forbidden",
                    "message": "当前角色无权访问该接口",
                },
            )
        return current_user

    return checker
