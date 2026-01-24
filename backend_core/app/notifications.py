from fastapi import APIRouter, Depends, HTTPException
from typing import List
from sqlalchemy.orm import Session

from . import models, schemas
from .deps import get_db, get_current_user, require_role

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/", response_model=List[schemas.NotificationOut])
def list_notifications(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    # return notifications addressed to the user, to their role, or global notifications
    q = db.query(models.Notification).filter(
        (models.Notification.user_id == current_user.id)
        | (models.Notification.role == current_user.role)
        | ((models.Notification.user_id == None) & (models.Notification.role == None))
    ).order_by(models.Notification.created_at.desc())
    return q.all()


@router.post("/mark-read")
def mark_read(ids: List[int], db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    if not ids:
        return {"updated": 0}
    q = db.query(models.Notification).filter(models.Notification.id.in_(ids))
    updated = 0
    for n in q.all():
        # only allow marking notifications that belong to this user or their role or global
        if n.user_id in (None, current_user.id) or n.role == current_user.role:
            n.read = True
            db.add(n)
            updated += 1
    db.commit()
    return {"updated": updated}


@router.post("/")
def create_notification(n_in: schemas.NotificationCreate, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss"))):
    n = models.Notification(user_id=n_in.user_id, role=n_in.role, message=n_in.message, level=n_in.level)
    db.add(n)
    db.commit()
    db.refresh(n)
    return {"created": n.id}


@router.get("/settings", response_model=schemas.NotificationSettingOut)
def get_my_settings(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    s = db.query(models.NotificationSetting).filter(models.NotificationSetting.user_id == current_user.id).first()
    if s:
        return s
    # fallback to role defaults
    r = db.query(models.RoleNotificationSetting).filter(models.RoleNotificationSetting.role == current_user.role).first()
    if r:
        return r
    # default
    return {
        "in_app": True,
        "email": False,
        "push": False,
        "instr_from_boss": True,
        "stage_changes": True,
        "query_raised": True,
        "query_response": True,
        "low_inventory": True,
        "dispatch_completed": True,
        "updated_at": None
    }


@router.put("/settings", response_model=schemas.NotificationSettingOut)
def update_my_settings(s_in: schemas.NotificationSettingIn, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    s = db.query(models.NotificationSetting).filter(models.NotificationSetting.user_id == current_user.id).first()
    if not s:
        s = models.NotificationSetting(user_id=current_user.id)
    if s_in.in_app is not None:
        s.in_app = s_in.in_app
    if s_in.email is not None:
        s.email = s_in.email
    if s_in.push is not None:
        s.push = s_in.push
    # per-event toggles
    if hasattr(s_in, 'instr_from_boss') and s_in.instr_from_boss is not None:
        s.instr_from_boss = s_in.instr_from_boss
    if hasattr(s_in, 'stage_changes') and s_in.stage_changes is not None:
        s.stage_changes = s_in.stage_changes
    if hasattr(s_in, 'query_raised') and s_in.query_raised is not None:
        s.query_raised = s_in.query_raised
    if hasattr(s_in, 'query_response') and s_in.query_response is not None:
        s.query_response = s_in.query_response
    if hasattr(s_in, 'low_inventory') and s_in.low_inventory is not None:
        s.low_inventory = s_in.low_inventory
    if hasattr(s_in, 'dispatch_completed') and s_in.dispatch_completed is not None:
        s.dispatch_completed = s_in.dispatch_completed
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.get("/roles/{role}", response_model=schemas.RoleNotificationSettingOut)
def get_role_settings(role: str, db: Session = Depends(get_db)):
    r = db.query(models.RoleNotificationSetting).filter(models.RoleNotificationSetting.role == role).first()
    if not r:
        raise HTTPException(status_code=404, detail="Role settings not found")
    return r


@router.put("/roles/{role}", response_model=schemas.RoleNotificationSettingOut)
def update_role_settings(role: str, s_in: schemas.RoleNotificationSettingIn, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    # allow only members of the role to update their role settings
    if current_user.role != role:
        raise HTTPException(status_code=403, detail="Can only update settings for your own role")
    r = db.query(models.RoleNotificationSetting).filter(models.RoleNotificationSetting.role == role).first()
    if not r:
        r = models.RoleNotificationSetting(role=role)
    if s_in.in_app is not None:
        r.in_app = s_in.in_app
    if s_in.email is not None:
        r.email = s_in.email
    if s_in.push is not None:
        r.push = s_in.push
    # per-event toggles
    if hasattr(s_in, 'instr_from_boss') and s_in.instr_from_boss is not None:
        r.instr_from_boss = s_in.instr_from_boss
    if hasattr(s_in, 'stage_changes') and s_in.stage_changes is not None:
        r.stage_changes = s_in.stage_changes
    if hasattr(s_in, 'query_raised') and s_in.query_raised is not None:
        r.query_raised = s_in.query_raised
    if hasattr(s_in, 'query_response') and s_in.query_response is not None:
        r.query_response = s_in.query_response
    if hasattr(s_in, 'low_inventory') and s_in.low_inventory is not None:
        r.low_inventory = s_in.low_inventory
    if hasattr(s_in, 'dispatch_completed') and s_in.dispatch_completed is not None:
        r.dispatch_completed = s_in.dispatch_completed
    db.add(r)
    db.commit()
    db.refresh(r)
    return r
