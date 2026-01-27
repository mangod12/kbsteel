"""
Dependencies Module - Re-exports security functions for backward compatibility.
The actual implementations are in security.py
"""
from typing import Generator
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from . import models
from .db import SessionLocal

# Re-export from security module for backward compatibility
from .security import (
    SECRET_KEY,
    ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    oauth2_scheme,
    verify_password,
    get_password_hash,
    create_access_token,
    decode_token,
    Permission,
    ROLE_PERMISSIONS,
    get_role_permissions,
    require_permission,
    RateLimiter,
    SecurityAuditLog,
    PasswordPolicy,
)


def get_db() -> Generator:
    """Database session dependency"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    """
    Get current authenticated user from JWT token.
    """
    payload = decode_token(token)
    
    username = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    user = db.query(models.User).filter(models.User.username == username).first()
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    if hasattr(user, 'is_active') and not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is disabled"
        )
    
    return user


def require_role(*allowed_roles: str):
    """
    Dependency that requires user to have one of the specified roles.
    """
    async def role_checker(current_user = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {', '.join(allowed_roles)}"
            )
        return current_user
    
    return role_checker
