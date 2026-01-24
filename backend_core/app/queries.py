from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from . import models, schemas
from .deps import get_db, require_role

router = APIRouter(prefix="/queries", tags=["queries"])


@router.post("", response_model=schemas.QueryOut, status_code=201)
def create_query(q_in: schemas.QueryCreate, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor", "User"))):
    # validate customer
    cust = db.query(models.Customer).filter(models.Customer.id == q_in.customer_id).first()
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found")
    query = models.Query(customer_id=q_in.customer_id, production_item_id=q_in.production_item_id, stage=q_in.stage, description=q_in.description, image_path=q_in.image_path, status="open")
    db.add(query)
    db.commit()
    db.refresh(query)
    return query


@router.get("", response_model=List[schemas.QueryOut])
def list_queries(db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor", "User"))):
    items = db.query(models.Query).all()
    return items
