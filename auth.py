import os
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import get_db, User

SECRET_KEY  = os.environ.get("SECRET_KEY", "change-me-in-production-please")
ALGORITHM   = "HS256"
EXPIRE_DAYS = 7
COOKIE_NAME = "ods_token"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_token(user_id: int, role: str) -> str:
    expire = datetime.utcnow() + timedelta(days=EXPIRE_DAYS)
    return jwt.encode(
        {"sub": str(user_id), "role": role, "exp": expire},
        SECRET_KEY, algorithm=ALGORITHM
    )


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def _user_from_request(request: Request, db: Session) -> Optional[User]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    return db.query(User).filter(User.id == int(payload["sub"])).first()


class RequireUser:
    """Dependency that redirects to /login if not authenticated."""
    def __call__(self, request: Request, db: Session = Depends(get_db)):
        user = _user_from_request(request, db)
        if not user:
            # Store intended destination for post-login redirect
            raise _LoginRedirect()
        return user


class RequireAdmin:
    """Dependency that requires admin role."""
    def __call__(self, request: Request, db: Session = Depends(get_db)):
        user = _user_from_request(request, db)
        if not user:
            raise _LoginRedirect()
        if user.role != "admin":
            raise _ForbiddenRedirect()
        return user


class _LoginRedirect(Exception):
    pass

class _ForbiddenRedirect(Exception):
    pass


get_current_user = RequireUser()
require_admin    = RequireAdmin()


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    return _user_from_request(request, db)
