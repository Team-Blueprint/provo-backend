import uuid
from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_access_token
from app.db.models import User
from app.services.auth_service import AuthService

http_bearer = HTTPBearer(auto_error=True)
auth_service = AuthService()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(http_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Standard JWT Bearer dependency:
    Extracts and validates the Bearer token and resolves the User from database.
    Integrates with OpenAPI / Swagger UI Authorize lock button automatically.
    """
    token = credentials.credentials
    try:
        payload = decode_access_token(token)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired access token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload missing subject identifier",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await auth_service.get_user_by_id(db, uuid.UUID(user_id_str))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


class RequireRole:
    """
    RBAC Permission dependency enforcing user roles/capabilities:
    - worker: user.is_worker
    - client: user.is_client
    - supervisor: user.is_supervisor
    - admin: user.is_admin
    """

    def __init__(self, *roles: str) -> None:
        self.roles = {r.lower() for r in roles}

    def __call__(self, user: User = Depends(get_current_user)) -> User:
        if user.is_admin:
            return user

        user_roles = set()
        if user.is_worker:
            user_roles.add("worker")
        if user.is_client:
            user_roles.add("client")
        if user.is_supervisor:
            user_roles.add("supervisor")
        if user.is_admin:
            user_roles.add("admin")

        if not (self.roles & user_roles):
            allowed = ", ".join(sorted(self.roles))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: action requires one of the following roles: [{allowed}]",
            )
        return user


# Common convenience role dependencies
require_worker: Callable = RequireRole("worker")
require_client: Callable = RequireRole("client")
require_admin: Callable = RequireRole("admin")
require_client_or_supervisor: Callable = RequireRole("client", "supervisor")
