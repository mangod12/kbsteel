from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session

from . import models, schemas
from .deps import get_db, verify_password, get_password_hash, create_access_token, require_role

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=schemas.Token)
async def login(request: Request, db: Session = Depends(get_db)):
    """Accept either form-encoded (OAuth2) login or JSON {username,password} for legacy frontend compatibility."""
    # Determine content type and extract credentials accordingly
    ctype = (request.headers.get("content-type") or "").lower()
    username = None
    password = None

    if "application/json" in ctype:
        try:
            body = await request.json()
            username = body.get("username")
            password = body.get("password")
        except Exception:
            pass
    else:
        # fallback to form data (OAuth2PasswordRequestForm expects form-encoded)
        try:
            form = await request.form()
            username = form.get("username")
            password = form.get("password")
        except Exception:
            pass

    if not username or not password:
        raise HTTPException(status_code=400, detail="Missing username or password")

    user = db.query(models.User).filter(models.User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect username or password")

    access_token = create_access_token({"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer", "role": user.role}


@router.post("/register", response_model=schemas.UserOut, dependencies=[Depends(require_role("Boss"))])
def register(user_in: schemas.UserCreate, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss"))):
    existing = db.query(models.User).filter((models.User.username == user_in.username) | (models.User.email == user_in.email)).first()
    if existing:
        raise HTTPException(status_code=400, detail="User with that username or email already exists")
    hashed = get_password_hash(user_in.password)
    user = models.User(full_name=user_in.full_name, email=user_in.email, username=user_in.username, password_hash=hashed, role=user_in.role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
