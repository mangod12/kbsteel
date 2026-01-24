from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from . import models, schemas
from .deps import get_db, require_role

router = APIRouter(prefix="/customers", tags=["customers"])


@router.post("", response_model=schemas.CustomerOut, status_code=201)
def create_customer(customer_in: schemas.CustomerCreate, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor"))):
    cust = models.Customer(name=customer_in.name, project_details=customer_in.project_details)
    db.add(cust)
    db.commit()
    db.refresh(cust)
    return cust


@router.get("", response_model=List[schemas.CustomerOut])
def list_customers(db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor", "User"))):
    customers = db.query(models.Customer).all()
    return customers


@router.post("/{customer_id}/items", response_model=schemas.ProductionItemOut, status_code=201)
def create_production_item(customer_id: int, item_in: schemas.ProductionItemCreate, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor"))):
    cust = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found")
    item = models.ProductionItem(customer_id=customer_id, item_code=item_in.item_code, item_name=item_in.item_name, section=item_in.section, length_mm=item_in.length_mm)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/{customer_id}/items", response_model=List[schemas.ProductionItemOut])
def list_production_items(customer_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor", "User"))):
    items = db.query(models.ProductionItem).filter(models.ProductionItem.customer_id == customer_id).all()
    return items
