from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from sqlalchemy import inspect
from datetime import datetime

from . import models, schemas
from .deps import get_db, require_role, get_current_user

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("/", response_model=List[schemas.InventoryOut])
def list_inventory(
    material_name: Optional[str] = Query(None, alias="material_name"),
    material_code: Optional[str] = Query(None, alias="material_code"),
    section: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    quantity_min: Optional[int] = Query(None),
    quantity_max: Optional[int] = Query(None),
    unit: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    List inventory with optional filters (all filters via query params).
    Empty / missing params return the full list.
    Filters are applied safely via SQLAlchemy expressions (no raw SQL).
    """
    query = db.query(models.Inventory)

    # name / material_name (partial, case-insensitive)
    if material_name:
        query = query.filter(models.Inventory.name.ilike(f"%{material_name}%"))

    # only apply filters that correspond to actual columns in the DB table
    try:
        inspector = inspect(db.bind)
        existing_cols = {c['name'] for c in inspector.get_columns(models.Inventory.__tablename__)}
    except Exception:
        existing_cols = set()

    # code
    if material_code and 'code' in existing_cols:
        query = query.filter(models.Inventory.code.ilike(f"%{material_code}%"))

    # section
    if section and 'section' in existing_cols:
        query = query.filter(models.Inventory.section.ilike(f"%{section}%"))

    # category
    if category and 'category' in existing_cols:
        query = query.filter(models.Inventory.category.ilike(f"%{category}%"))

    # unit exact match (if provided)
    if unit:
        query = query.filter(models.Inventory.unit == unit)

    # quantity filters operate on remaining = total - used
    rem_expr = (models.Inventory.total - models.Inventory.used)
    if quantity_min is not None:
        query = query.filter(rem_expr >= quantity_min)
    if quantity_max is not None:
        query = query.filter(rem_expr <= quantity_max)

    # date range: requires created_at column to exist in DB
    if (date_from or date_to) and 'created_at' in existing_cols:
        try:
            if date_from:
                dt_from = datetime.fromisoformat(date_from)
                query = query.filter(models.Inventory.created_at >= dt_from)
            if date_to:
                dt_to = datetime.fromisoformat(date_to)
                query = query.filter(models.Inventory.created_at <= dt_to)
        except ValueError:
            # ignore invalid dates instead of failing the whole request
            pass

    items = query.all()
    return items


@router.post("/", response_model=schemas.InventoryOut)
def create_item(item_in: schemas.InventoryIn, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor"))):
    """
    Create a new inventory item.
    
    IMPORTANT: This is a simplified model. For production steel industry use,
    see the improved models in models_v2.py and routers/inventory_v2.py
    """
    # Validate that used doesn't exceed total
    if item_in.used > item_in.total:
        raise HTTPException(
            status_code=400,
            detail="Used quantity cannot exceed total quantity"
        )
    
    if item_in.total < 0 or item_in.used < 0:
        raise HTTPException(
            status_code=400,
            detail="Quantities cannot be negative"
        )
    
    # Only include optional fields if the DB table actually has those columns
    try:
        inspector = inspect(db.bind)
        existing_cols = {c['name'] for c in inspector.get_columns(models.Inventory.__tablename__)}
    except Exception:
        existing_cols = set()

    item_kwargs = dict(
        name=item_in.name.strip(),  # Sanitize input
        unit=item_in.unit.strip() if item_in.unit else None,
        total=item_in.total,
        used=item_in.used,
    )
    if 'code' in existing_cols:
        item_kwargs['code'] = getattr(item_in, 'code', None)
    if 'section' in existing_cols:
        item_kwargs['section'] = getattr(item_in, 'section', None)
    if 'category' in existing_cols:
        item_kwargs['category'] = getattr(item_in, 'category', None)

    try:
        item = models.Inventory(**item_kwargs)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create inventory item: {str(e)}"
        )


@router.put("/{item_id}", response_model=schemas.InventoryOut)
def update_item(item_id: int, item_in: schemas.InventoryIn, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor"))):
    """
    Update an inventory item.
    
    WARNING: This does NOT create an audit trail. For production use,
    implement proper stock movement tracking as in inventory_service.py
    """
    # Validate input
    if item_in.used > item_in.total:
        raise HTTPException(
            status_code=400,
            detail="Used quantity cannot exceed total quantity"
        )
    
    if item_in.total < 0 or item_in.used < 0:
        raise HTTPException(
            status_code=400,
            detail="Quantities cannot be negative"
        )
    
    # Use SELECT FOR UPDATE to prevent race conditions
    # Note: SQLite doesn't support this, but PostgreSQL/MySQL do
    try:
        i = db.query(models.Inventory).filter(
            models.Inventory.id == item_id
        ).with_for_update(nowait=True).first()
    except Exception:
        # Fallback for SQLite
        i = db.query(models.Inventory).filter(models.Inventory.id == item_id).first()
    
    if not i:
        raise HTTPException(status_code=404, detail="Item not found")
    
    # Store old values for audit (in production, log this properly)
    old_total = i.total
    old_used = i.used
    
    i.name = item_in.name.strip() if item_in.name else i.name
    i.unit = item_in.unit.strip() if item_in.unit else i.unit
    i.total = item_in.total
    i.used = item_in.used
    
    try:
        inspector = inspect(db.bind)
        existing_cols = {c['name'] for c in inspector.get_columns(models.Inventory.__tablename__)}
    except Exception:
        existing_cols = set()

    if 'code' in existing_cols:
        i.code = getattr(item_in, 'code', None)
    if 'section' in existing_cols:
        i.section = getattr(item_in, 'section', None)
    if 'category' in existing_cols:
        i.category = getattr(item_in, 'category', None)
    
    try:
        db.add(i)
        db.commit()
        db.refresh(i)
        
        # Log significant changes (TODO: implement proper audit logging)
        if old_total != i.total or old_used != i.used:
            print(f"[AUDIT] Item {item_id} updated by user {current_user.id}: "
                  f"total {old_total}->{i.total}, used {old_used}->{i.used}")
        
        return i
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update inventory item: {str(e)}"
        )


@router.delete("/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor"))):
    i = db.query(models.Inventory).filter(models.Inventory.id == item_id).first()
    if not i:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(i)
    db.commit()
    return {"message": "deleted"}
