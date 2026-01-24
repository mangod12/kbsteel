from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .deps import get_db, get_current_user, verify_password, get_password_hash
from . import models, schemas

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=schemas.UserOut)
def me_user(current_user: models.User = Depends(get_current_user)):
    return current_user


@router.post("/change-password")
def change_password(pw: schemas.ChangePasswordIn, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    if not verify_password(pw.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Old password is incorrect")
    if not pw.new_password or len(pw.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters long")
    current_user.password_hash = get_password_hash(pw.new_password)
    db.add(current_user)
    db.commit()
    return {"status": "ok", "message": "Password updated"}
