"""HTTP Basic auth against APP_USER / APP_PASSWORD (same as mobile app)."""
from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()


def require_app_user(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    user = os.environ.get("APP_USER", "povilika_app")
    password = os.environ.get("APP_PASSWORD", "")
    ok_user = secrets.compare_digest(credentials.username, user)
    ok_pass = secrets.compare_digest(credentials.password, password)
    if not (ok_user and ok_pass) or not password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
