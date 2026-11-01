from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import (
    get_db,
    require_authenticated_user,
    require_csrf,
)
from app.core.rate_limit import rate_limit
from app.core.security import create_csrf_token
from app.modules.auth.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    RegistrationAccepted,
)
from app.modules.auth.services.auth_service import AuthService
from app.modules.users.models.user import User
from app.modules.users.schemas.user import UserRead


router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookie(
    response: Response,
    name: str,
    value: str,
    *,
    max_age: int,
    http_only: bool,
    path: str = "/",
) -> None:
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        httponly=http_only,
        secure=settings.secure_cookies,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN,
        path=path,
    )


def _set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
) -> None:
    _set_cookie(
        response,
        settings.ACCESS_COOKIE_NAME,
        access_token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        http_only=True,
    )
    _set_cookie(
        response,
        settings.REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        http_only=True,
        path="/api/v1/auth",
    )
    _set_cookie(
        response,
        settings.CSRF_COOKIE_NAME,
        create_csrf_token(),
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        http_only=False,
    )


@router.get("/csrf")
def issue_csrf_token(response: Response) -> dict[str, str]:
    csrf_token = create_csrf_token()
    _set_cookie(
        response,
        settings.CSRF_COOKIE_NAME,
        csrf_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        http_only=False,
    )
    return {"message": "CSRF token issued"}


@router.post(
    "/register",
    response_model=RegistrationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_csrf), Depends(rate_limit("register"))],
)
def register(
    payload: RegisterRequest,
    session: Session = Depends(get_db),
) -> RegistrationAccepted:
    AuthService(session).register(payload)
    return RegistrationAccepted()


@router.post(
    "/login",
    response_model=UserRead,
    dependencies=[Depends(require_csrf), Depends(rate_limit("login"))],
)
def login(
    payload: LoginRequest,
    response: Response,
    session: Session = Depends(get_db),
) -> User:
    user, access_token, refresh_token = AuthService(session).login(payload)
    _set_auth_cookies(response, access_token, refresh_token)
    return user


@router.post(
    "/refresh",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_csrf), Depends(rate_limit("refresh"))],
)
def refresh(
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
) -> dict[str, str]:
    raw_token = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is invalid or expired",
        )
    _, access_token, rotated_token = AuthService(session).refresh(raw_token)
    _set_auth_cookies(response, access_token, rotated_token)
    return {"message": "Tokens refreshed"}


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
) -> Response:
    AuthService(session).logout(request.cookies.get(settings.REFRESH_COOKIE_NAME))
    result = JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)
    result.delete_cookie(
        settings.ACCESS_COOKIE_NAME,
        path="/",
        domain=settings.COOKIE_DOMAIN,
        secure=settings.secure_cookies,
        httponly=True,
        samesite=settings.COOKIE_SAMESITE,
    )
    result.delete_cookie(
        settings.REFRESH_COOKIE_NAME,
        path="/api/v1/auth",
        domain=settings.COOKIE_DOMAIN,
        secure=settings.secure_cookies,
        httponly=True,
        samesite=settings.COOKIE_SAMESITE,
    )
    result.delete_cookie(
        settings.CSRF_COOKIE_NAME,
        path="/",
        domain=settings.COOKIE_DOMAIN,
        secure=settings.secure_cookies,
        httponly=False,
        samesite=settings.COOKIE_SAMESITE,
    )
    return result


@router.get("/me", response_model=UserRead)
def current_user(user: User = Depends(require_authenticated_user)) -> User:
    return user
