import secrets

import structlog
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from dmo.config import settings

logger = structlog.get_logger()

security = HTTPBasic(auto_error=True, realm="DMO Admin")


async def verify_admin(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    correct_user = settings.admin_username
    correct_pass = settings.admin_password
    if correct_user == "admin" and correct_pass == "admin":
        logger.warning("admin_default_credentials_in_use")
    if not secrets.compare_digest(credentials.username, correct_user):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not secrets.compare_digest(credentials.password, correct_pass):
        raise HTTPException(status_code=401, detail="Invalid credentials")
