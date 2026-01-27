"""
Improved Security Module for Steel Industry ERP
===============================================
Addresses critical security vulnerabilities:
- Proper secret key management
- Secure password policies
- Rate limiting
- Audit logging
- Role-based access control with fine-grained permissions

Author: System Architect Review
"""

import os
import re
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, List, Set
from functools import wraps

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
import bcrypt
from pydantic import BaseModel, validator
from sqlalchemy.orm import Session

from .db import SessionLocal


# =============================================================================
# CONFIGURATION - Secure Defaults
# =============================================================================

def get_secret_key() -> str:
    """
    Get secret key from environment with validation.
    NEVER use a default secret key in production!
    """
    secret = os.getenv("KUMAR_SECRET_KEY")
    
    if not secret:
        # Check if we're in development mode
        env = os.getenv("ENVIRONMENT", "development")
        if env == "production":
            raise RuntimeError(
                "CRITICAL: KUMAR_SECRET_KEY environment variable must be set in production! "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        # Development mode - generate a temporary key with warning
        import warnings
        warnings.warn(
            "⚠️  Using auto-generated secret key. Set KUMAR_SECRET_KEY for production!",
            RuntimeWarning
        )
        # Use a deterministic key in development for session persistence during hot-reload
        secret = hashlib.sha256(b"dev-mode-insecure-key").hexdigest()
    
    # Validate key strength
    if len(secret) < 32:
        raise RuntimeError("KUMAR_SECRET_KEY must be at least 32 characters")
    
    return secret


SECRET_KEY = get_secret_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("TOKEN_EXPIRE_MINUTES", "60"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))


# =============================================================================
# PASSWORD SECURITY
# =============================================================================

# Password policy constants
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
PASSWORD_PATTERN = re.compile(
    r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]'
)


class PasswordPolicy:
    """Password strength validation"""
    
    @staticmethod
    def validate(password: str) -> tuple[bool, List[str]]:
        """
        Validate password against security policy.
        
        Returns:
            (is_valid, list_of_errors)
        """
        errors = []
        
        if len(password) < MIN_PASSWORD_LENGTH:
            errors.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
        
        if len(password) > MAX_PASSWORD_LENGTH:
            errors.append(f"Password must not exceed {MAX_PASSWORD_LENGTH} characters")
        
        if not re.search(r'[a-z]', password):
            errors.append("Password must contain at least one lowercase letter")
        
        if not re.search(r'[A-Z]', password):
            errors.append("Password must contain at least one uppercase letter")
        
        if not re.search(r'\d', password):
            errors.append("Password must contain at least one digit")
        
        if not re.search(r'[@$!%*?&]', password):
            errors.append("Password must contain at least one special character (@$!%*?&)")
        
        # Check for common weak passwords
        common_passwords = {'password', 'password123', '12345678', 'qwerty123'}
        if password.lower() in common_passwords:
            errors.append("Password is too common")
        
        return len(errors) == 0, errors


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))


def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')


# =============================================================================
# TOKEN MANAGEMENT
# =============================================================================

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


class TokenData(BaseModel):
    """Token payload structure"""
    sub: str  # username
    role: str
    permissions: List[str] = []
    exp: datetime
    iat: datetime
    jti: str  # JWT ID for token revocation


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create a JWT access token with security best practices.
    """
    to_encode = data.copy()
    
    now = datetime.utcnow()
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    
    # Add security claims
    to_encode.update({
        "exp": expire,
        "iat": now,
        "jti": secrets.token_urlsafe(16),  # Unique token ID
        "type": "access"
    })
    
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(username: str) -> str:
    """
    Create a refresh token for token renewal.
    """
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    
    to_encode = {
        "sub": username,
        "exp": expire,
        "iat": datetime.utcnow(),
        "jti": secrets.token_urlsafe(16),
        "type": "refresh"
    }
    
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT token.
    
    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"}
        )


# =============================================================================
# ROLE-BASED ACCESS CONTROL (RBAC)
# =============================================================================

class Permission:
    """Fine-grained permissions for steel industry operations"""
    
    # Inventory permissions
    INVENTORY_VIEW = "inventory:view"
    INVENTORY_CREATE = "inventory:create"
    INVENTORY_UPDATE = "inventory:update"
    INVENTORY_DELETE = "inventory:delete"
    INVENTORY_ADJUST = "inventory:adjust"  # Stock adjustments
    
    # GRN permissions
    GRN_VIEW = "grn:view"
    GRN_CREATE = "grn:create"
    GRN_APPROVE = "grn:approve"
    
    # Dispatch permissions
    DISPATCH_VIEW = "dispatch:view"
    DISPATCH_CREATE = "dispatch:create"
    DISPATCH_APPROVE = "dispatch:approve"
    
    # QA permissions
    QA_VIEW = "qa:view"
    QA_INSPECT = "qa:inspect"
    QA_APPROVE = "qa:approve"
    QA_REJECT = "qa:reject"
    QA_HOLD = "qa:hold"
    
    # Production permissions
    PRODUCTION_VIEW = "production:view"
    PRODUCTION_UPDATE = "production:update"
    PRODUCTION_CONSUME = "production:consume"  # Material consumption
    
    # Admin permissions
    USER_VIEW = "user:view"
    USER_CREATE = "user:create"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"
    
    REPORT_VIEW = "report:view"
    REPORT_EXPORT = "report:export"
    
    SETTINGS_VIEW = "settings:view"
    SETTINGS_UPDATE = "settings:update"


# Role definitions with their permissions
ROLE_PERMISSIONS: dict[str, Set[str]] = {
    "Boss": {
        # Full access
        Permission.INVENTORY_VIEW, Permission.INVENTORY_CREATE, 
        Permission.INVENTORY_UPDATE, Permission.INVENTORY_DELETE,
        Permission.INVENTORY_ADJUST,
        Permission.GRN_VIEW, Permission.GRN_CREATE, Permission.GRN_APPROVE,
        Permission.DISPATCH_VIEW, Permission.DISPATCH_CREATE, Permission.DISPATCH_APPROVE,
        Permission.QA_VIEW, Permission.QA_INSPECT, Permission.QA_APPROVE,
        Permission.QA_REJECT, Permission.QA_HOLD,
        Permission.PRODUCTION_VIEW, Permission.PRODUCTION_UPDATE, Permission.PRODUCTION_CONSUME,
        Permission.USER_VIEW, Permission.USER_CREATE, Permission.USER_UPDATE, Permission.USER_DELETE,
        Permission.REPORT_VIEW, Permission.REPORT_EXPORT,
        Permission.SETTINGS_VIEW, Permission.SETTINGS_UPDATE,
    },
    
    "Software Supervisor": {
        Permission.INVENTORY_VIEW, Permission.INVENTORY_CREATE, 
        Permission.INVENTORY_UPDATE, Permission.INVENTORY_ADJUST,
        Permission.GRN_VIEW, Permission.GRN_CREATE, Permission.GRN_APPROVE,
        Permission.DISPATCH_VIEW, Permission.DISPATCH_CREATE, Permission.DISPATCH_APPROVE,
        Permission.QA_VIEW,
        Permission.PRODUCTION_VIEW, Permission.PRODUCTION_UPDATE, Permission.PRODUCTION_CONSUME,
        Permission.REPORT_VIEW, Permission.REPORT_EXPORT,
        Permission.SETTINGS_VIEW,
    },
    
    "Store Keeper": {
        Permission.INVENTORY_VIEW, Permission.INVENTORY_CREATE, Permission.INVENTORY_UPDATE,
        Permission.GRN_VIEW, Permission.GRN_CREATE,
        Permission.DISPATCH_VIEW, Permission.DISPATCH_CREATE,
        Permission.PRODUCTION_VIEW, Permission.PRODUCTION_CONSUME,
        Permission.REPORT_VIEW,
    },
    
    "QA Inspector": {
        Permission.INVENTORY_VIEW,
        Permission.GRN_VIEW,
        Permission.QA_VIEW, Permission.QA_INSPECT, Permission.QA_APPROVE,
        Permission.QA_REJECT, Permission.QA_HOLD,
        Permission.REPORT_VIEW,
    },
    
    "Dispatch Operator": {
        Permission.INVENTORY_VIEW,
        Permission.DISPATCH_VIEW, Permission.DISPATCH_CREATE,
        Permission.PRODUCTION_VIEW,
        Permission.REPORT_VIEW,
    },
    
    "User": {
        Permission.INVENTORY_VIEW,
        Permission.GRN_VIEW,
        Permission.DISPATCH_VIEW,
        Permission.PRODUCTION_VIEW,
        Permission.REPORT_VIEW,
    },
}


def get_role_permissions(role: str) -> Set[str]:
    """Get permissions for a role"""
    return ROLE_PERMISSIONS.get(role, set())


# =============================================================================
# DEPENDENCY INJECTION
# =============================================================================

def get_db():
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
    from . import models  # Avoid circular import
    
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


def require_permission(*required_permissions: str):
    """
    Dependency that requires user to have specific permissions.
    More granular than role-based checks.
    """
    async def permission_checker(current_user = Depends(get_current_user)):
        user_permissions = get_role_permissions(current_user.role)
        
        missing = set(required_permissions) - user_permissions
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permissions: {', '.join(missing)}"
            )
        
        return current_user
    
    return permission_checker


# =============================================================================
# AUDIT LOGGING
# =============================================================================

class SecurityAuditLog:
    """Security event logging for compliance and forensics"""
    
    @staticmethod
    def log_login_attempt(
        db: Session,
        username: str,
        success: bool,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        failure_reason: Optional[str] = None
    ):
        """Log a login attempt"""
        from .models_v2 import AuditLog
        import json
        
        log = AuditLog(
            entity_type="auth",
            entity_id=0,
            action="login_attempt",
            new_values=json.dumps({
                "username": username,
                "success": success,
                "failure_reason": failure_reason,
                "ip_address": ip_address,
                "user_agent": user_agent
            }),
            ip_address=ip_address,
            user_agent=user_agent
        )
        db.add(log)
        db.commit()
    
    @staticmethod
    def log_sensitive_action(
        db: Session,
        user_id: int,
        action: str,
        entity_type: str,
        entity_id: int,
        details: dict,
        ip_address: Optional[str] = None
    ):
        """Log a sensitive operation for audit trail"""
        from .models_v2 import AuditLog
        import json
        
        log = AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            new_values=json.dumps(details),
            user_id=user_id,
            ip_address=ip_address
        )
        db.add(log)
        db.commit()


# =============================================================================
# RATE LIMITING
# =============================================================================

class RateLimiter:
    """
    Simple in-memory rate limiter.
    In production, use Redis for distributed rate limiting.
    """
    
    _attempts: dict[str, List[datetime]] = {}
    
    @classmethod
    def check_rate_limit(
        cls,
        key: str,
        max_attempts: int = 5,
        window_seconds: int = 300
    ) -> tuple[bool, int]:
        """
        Check if action is rate limited.
        
        Returns:
            (is_allowed, remaining_attempts)
        """
        now = datetime.utcnow()
        window_start = now - timedelta(seconds=window_seconds)
        
        # Clean old attempts
        if key in cls._attempts:
            cls._attempts[key] = [
                t for t in cls._attempts[key] 
                if t > window_start
            ]
        else:
            cls._attempts[key] = []
        
        attempts = len(cls._attempts[key])
        remaining = max(0, max_attempts - attempts)
        
        if attempts >= max_attempts:
            return False, 0
        
        return True, remaining
    
    @classmethod
    def record_attempt(cls, key: str):
        """Record an attempt"""
        if key not in cls._attempts:
            cls._attempts[key] = []
        cls._attempts[key].append(datetime.utcnow())


# =============================================================================
# INPUT VALIDATION
# =============================================================================

def sanitize_input(value: str) -> str:
    """
    Sanitize user input to prevent injection attacks.
    SQLAlchemy handles SQL injection, but this provides defense in depth.
    """
    if not isinstance(value, str):
        return value
    
    # Remove null bytes
    value = value.replace('\x00', '')
    
    # Trim whitespace
    value = value.strip()
    
    return value


def validate_email(email: str) -> bool:
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))
