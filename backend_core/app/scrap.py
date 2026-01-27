"""
Scrap and Reusable Inventory Management
Handles scrap recording, reusable stock tracking, and loss analytics
"""
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Query
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from datetime import datetime, timedelta
from pydantic import BaseModel
import pandas as pd
from io import BytesIO

from . import models
from .deps import get_db, require_role, get_current_user

router = APIRouter(prefix="/scrap", tags=["scrap"])


# ============ Pydantic Schemas ============

class ScrapRecordCreate(BaseModel):
    material_name: str
    weight_kg: float
    reason_code: str  # cutting_waste, defect, damage, overrun, leftover
    length_mm: Optional[float] = None
    width_mm: Optional[float] = None
    quantity: int = 1
    source_item_id: Optional[int] = None
    source_customer_id: Optional[int] = None
    dimensions: Optional[str] = None
    notes: Optional[str] = None


class ScrapRecordOut(BaseModel):
    id: int
    material_name: str
    weight_kg: float
    length_mm: Optional[float]
    width_mm: Optional[float]
    quantity: int
    reason_code: str
    source_item_id: Optional[int]
    source_customer_id: Optional[int]
    dimensions: Optional[str]
    notes: Optional[str]
    status: str
    scrap_value: Optional[float]
    created_by: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True


class ReusableStockCreate(BaseModel):
    material_name: str
    dimensions: str
    weight_kg: float
    length_mm: Optional[float] = None
    width_mm: Optional[float] = None
    quantity: int = 1
    source_item_id: Optional[int] = None
    source_customer_id: Optional[int] = None
    quality_grade: str = "A"
    notes: Optional[str] = None


class ReusableStockOut(BaseModel):
    id: int
    material_name: str
    dimensions: str
    weight_kg: float
    length_mm: Optional[float]
    width_mm: Optional[float]
    quantity: int
    source_item_id: Optional[int]
    source_customer_id: Optional[int]
    quality_grade: str
    notes: Optional[str]
    is_available: bool
    used_in_item_id: Optional[int]
    created_by: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True


class ScrapCSVRow(BaseModel):
    material_name: str
    dimensions: str
    weight_kg: float
    quantity: int
    reason_code: str


# ============ Scrap Endpoints ============

@router.get("/records", response_model=List[ScrapRecordOut])
def list_scrap_records(
    status: Optional[str] = Query(None),
    reason_code: Optional[str] = Query(None),
    material_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """List all scrap records with optional filters"""
    query = db.query(models.ScrapRecord)
    
    if status:
        query = query.filter(models.ScrapRecord.status == status)
    if reason_code:
        query = query.filter(models.ScrapRecord.reason_code == reason_code)
    if material_name:
        query = query.filter(models.ScrapRecord.material_name.ilike(f"%{material_name}%"))
    
    return query.order_by(models.ScrapRecord.created_at.desc()).all()


@router.post("/records", response_model=ScrapRecordOut)
def create_scrap_record(
    data: ScrapRecordCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Record new scrap material"""
    if data.weight_kg <= 0:
        raise HTTPException(status_code=400, detail="Weight must be positive")
    
    record = models.ScrapRecord(
        material_name=data.material_name,
        weight_kg=data.weight_kg,
        length_mm=data.length_mm,
        width_mm=data.width_mm,
        quantity=data.quantity,
        reason_code=data.reason_code,
        source_item_id=data.source_item_id,
        source_customer_id=data.source_customer_id,
        dimensions=data.dimensions,
        notes=data.notes,
        status="pending",
        created_by=current_user.id,
        created_at=datetime.utcnow(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.post("/upload-csv")
async def upload_scrap_csv(
    file: UploadFile = File(...),
    customer_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Upload CSV of scrap items after dispatch.
    CSV columns: material_name, dimensions, weight_kg, quantity, reason_code
    Returns grouped similar items for review.
    """
    if not file.filename.endswith(('.csv', '.xlsx')):
        raise HTTPException(status_code=400, detail="Only CSV and Excel files supported")
    
    content = await file.read()
    
    try:
        if file.filename.endswith('.xlsx'):
            df = pd.read_excel(BytesIO(content))
        else:
            # Try different encodings
            for encoding in ['utf-8', 'latin-1', 'cp1252']:
                try:
                    df = pd.read_csv(BytesIO(content), encoding=encoding)
                    break
                except:
                    continue
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")
    
    # Normalize column names
    df.columns = [c.lower().strip().replace(' ', '_') for c in df.columns]
    
    # Map common column names
    col_mapping = {
        'material': 'material_name', 'name': 'material_name', 'item': 'material_name',
        'profile': 'material_name', 'section': 'material_name',
        'dimension': 'dimensions', 'size': 'dimensions', 'dims': 'dimensions',
        'weight': 'weight_kg', 'wt': 'weight_kg', 'kg': 'weight_kg',
        'qty': 'quantity', 'pcs': 'quantity', 'pieces': 'quantity', 'nos': 'quantity',
        'reason': 'reason_code', 'type': 'reason_code', 'waste_type': 'reason_code',
        'length': 'length_mm', 'len': 'length_mm',
        'width': 'width_mm',
    }
    
    for old_col, new_col in col_mapping.items():
        if old_col in df.columns and new_col not in df.columns:
            df.rename(columns={old_col: new_col}, inplace=True)
    
    # Validate required columns
    required_cols = ['material_name', 'weight_kg']
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing columns: {missing}")
    
    # Process and group similar items
    records_created = []
    grouped_items = {}
    
    for _, row in df.iterrows():
        material = str(row.get('material_name', '')).strip()
        if not material or material == 'nan':
            continue
        
        weight = float(row.get('weight_kg', 0) or 0)
        quantity = int(row.get('quantity', 1) or 1)
        dimensions = str(row.get('dimensions', '') or '')
        reason = str(row.get('reason_code', 'leftover') or 'leftover')
        length_mm = float(row.get('length_mm', 0) or 0) if 'length_mm' in df.columns else None
        width_mm = float(row.get('width_mm', 0) or 0) if 'width_mm' in df.columns else None
        
        # Create scrap record
        record = models.ScrapRecord(
            material_name=material,
            weight_kg=weight,
            length_mm=length_mm,
            width_mm=width_mm,
            quantity=quantity,
            dimensions=dimensions,
            reason_code=reason,
            source_customer_id=customer_id,
            status="pending",
            created_by=current_user.id,
            created_at=datetime.utcnow(),
        )
        db.add(record)
        records_created.append(record)
        
        # Group by material and approximate dimensions
        group_key = f"{material}|{dimensions}"
        if group_key not in grouped_items:
            grouped_items[group_key] = {
                'material_name': material,
                'dimensions': dimensions,
                'total_weight_kg': 0,
                'total_quantity': 0,
                'records': []
            }
        grouped_items[group_key]['total_weight_kg'] += weight
        grouped_items[group_key]['total_quantity'] += quantity
    
    db.commit()
    
    # Convert grouped items to list
    grouped_list = list(grouped_items.values())
    
    return {
        "message": f"Imported {len(records_created)} scrap records",
        "records_count": len(records_created),
        "grouped_items": grouped_list,
        "total_weight_kg": sum(r.weight_kg for r in records_created),
    }


@router.put("/records/{record_id}/status")
def update_scrap_status(
    record_id: int,
    status: str,
    scrap_value: Optional[float] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("Boss", "Software Supervisor", "Store Keeper")),
):
    """Update scrap record status"""
    record = db.query(models.ScrapRecord).filter(models.ScrapRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Scrap record not found")
    
    valid_statuses = ["pending", "returned_to_inventory", "disposed", "recycled", "sold"]
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Status must be one of: {valid_statuses}")
    
    record.status = status
    if scrap_value is not None:
        record.scrap_value = scrap_value
    
    db.commit()
    return {"message": "Status updated", "id": record_id, "status": status}


@router.put("/records/{record_id}/return-to-inventory")
def return_scrap_to_inventory(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("Boss", "Software Supervisor", "Store Keeper")),
):
    """Return scrap back to main inventory (for reusable pieces)"""
    record = db.query(models.ScrapRecord).filter(models.ScrapRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Scrap record not found")
    
    if record.status == "returned_to_inventory":
        raise HTTPException(status_code=400, detail="Already returned to inventory")
    
    # Find matching inventory item or create new
    inv = db.query(models.Inventory).filter(
        models.Inventory.name.ilike(f"%{record.material_name}%")
    ).first()
    
    if inv:
        inv.total = (inv.total or 0) + record.weight_kg
    else:
        inv = models.Inventory(
            name=record.material_name,
            unit="kg",
            total=record.weight_kg,
            used=0,
            category="reusable",
            created_at=datetime.utcnow(),
        )
        db.add(inv)
    
    record.status = "returned_to_inventory"
    db.commit()
    
    return {
        "message": "Returned to inventory",
        "material": record.material_name,
        "weight_kg": record.weight_kg,
        "inventory_id": inv.id
    }


@router.put("/records/{record_id}/move-to-reusable")
def move_to_reusable(
    record_id: int,
    quality_grade: str = "A",
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Move scrap to reusable stock (for offcuts that can be used later)"""
    record = db.query(models.ScrapRecord).filter(models.ScrapRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Scrap record not found")
    
    # Create reusable stock entry
    reusable = models.ReusableStock(
        material_name=record.material_name,
        dimensions=record.dimensions or f"{record.length_mm}mm x {record.width_mm}mm",
        weight_kg=record.weight_kg,
        length_mm=record.length_mm,
        width_mm=record.width_mm,
        quantity=record.quantity,
        source_item_id=record.source_item_id,
        source_customer_id=record.source_customer_id,
        quality_grade=quality_grade,
        is_available=True,
        created_by=current_user.id,
        created_at=datetime.utcnow(),
    )
    db.add(reusable)
    
    record.status = "returned_to_inventory"
    db.commit()
    db.refresh(reusable)
    
    return {
        "message": "Moved to reusable stock",
        "reusable_id": reusable.id,
        "material": record.material_name,
    }


@router.delete("/records/{record_id}")
def delete_scrap_record(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("Boss", "Software Supervisor")),
):
    """Delete a scrap record (admin only)"""
    record = db.query(models.ScrapRecord).filter(models.ScrapRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Scrap record not found")
    
    db.delete(record)
    db.commit()
    return {"message": "Scrap record deleted", "id": record_id}


# ============ Reusable Stock Endpoints ============

@router.get("/reusable", response_model=List[ReusableStockOut])
def list_reusable_stock(
    available_only: bool = Query(True),
    material_name: Optional[str] = Query(None),
    quality_grade: Optional[str] = Query(None),
    min_length: Optional[float] = Query(None),
    max_length: Optional[float] = Query(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """List reusable stock items with filters"""
    query = db.query(models.ReusableStock)
    
    if available_only:
        query = query.filter(models.ReusableStock.is_available == True)
    if material_name:
        query = query.filter(models.ReusableStock.material_name.ilike(f"%{material_name}%"))
    if quality_grade:
        query = query.filter(models.ReusableStock.quality_grade == quality_grade)
    if min_length:
        query = query.filter(models.ReusableStock.length_mm >= min_length)
    if max_length:
        query = query.filter(models.ReusableStock.length_mm <= max_length)
    
    return query.order_by(models.ReusableStock.created_at.desc()).all()


@router.get("/reusable/find-match")
def find_matching_reusable(
    material_name: str,
    required_length_mm: float,
    tolerance_mm: float = 50,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Find reusable stock that matches required dimensions (for backfill)"""
    # Find available reusable items of similar material and sufficient length
    matches = db.query(models.ReusableStock).filter(
        models.ReusableStock.is_available == True,
        models.ReusableStock.material_name.ilike(f"%{material_name}%"),
        models.ReusableStock.length_mm >= required_length_mm - tolerance_mm,
    ).order_by(
        # Prefer closest match (smallest waste)
        func.abs(models.ReusableStock.length_mm - required_length_mm)
    ).limit(5).all()
    
    return {
        "required_length_mm": required_length_mm,
        "tolerance_mm": tolerance_mm,
        "matches": [
            {
                "id": m.id,
                "material_name": m.material_name,
                "dimensions": m.dimensions,
                "length_mm": m.length_mm,
                "weight_kg": m.weight_kg,
                "quality_grade": m.quality_grade,
                "waste_mm": (m.length_mm or 0) - required_length_mm,
            }
            for m in matches
        ]
    }


@router.post("/reusable", response_model=ReusableStockOut)
def create_reusable_stock(
    data: ReusableStockCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Add new reusable stock item"""
    if data.weight_kg <= 0:
        raise HTTPException(status_code=400, detail="Weight must be positive")
    
    stock = models.ReusableStock(
        material_name=data.material_name,
        dimensions=data.dimensions,
        weight_kg=data.weight_kg,
        length_mm=data.length_mm,
        width_mm=data.width_mm,
        quantity=data.quantity,
        source_item_id=data.source_item_id,
        source_customer_id=data.source_customer_id,
        quality_grade=data.quality_grade,
        notes=data.notes,
        is_available=True,
        created_by=current_user.id,
        created_at=datetime.utcnow(),
    )
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


@router.put("/reusable/{stock_id}/use")
def use_reusable_stock(
    stock_id: int,
    production_item_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Mark reusable stock as used in a production item"""
    stock = db.query(models.ReusableStock).filter(models.ReusableStock.id == stock_id).first()
    if not stock:
        raise HTTPException(status_code=404, detail="Reusable stock not found")
    if not stock.is_available:
        raise HTTPException(status_code=400, detail="Stock already used")
    
    stock.is_available = False
    stock.used_in_item_id = production_item_id
    db.commit()
    return {"message": "Stock marked as used", "id": stock_id}


@router.put("/reusable/{stock_id}/return-to-inventory")
def return_reusable_to_inventory(
    stock_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("Boss", "Software Supervisor", "Store Keeper")),
):
    """Return reusable stock back to main inventory"""
    stock = db.query(models.ReusableStock).filter(models.ReusableStock.id == stock_id).first()
    if not stock:
        raise HTTPException(status_code=404, detail="Reusable stock not found")
    if not stock.is_available:
        raise HTTPException(status_code=400, detail="Stock already used, cannot return")
    
    inv = db.query(models.Inventory).filter(
        models.Inventory.name.ilike(f"%{stock.material_name}%")
    ).first()
    
    if inv:
        inv.total = (inv.total or 0) + stock.weight_kg
    else:
        inv = models.Inventory(
            name=stock.material_name,
            unit="kg",
            total=stock.weight_kg,
            used=0,
            category="reusable",
            created_at=datetime.utcnow(),
        )
        db.add(inv)
    
    stock.is_available = False
    stock.notes = (stock.notes or "") + f" [Returned to inventory {datetime.utcnow().strftime('%Y-%m-%d')}]"
    
    db.commit()
    return {"message": "Returned to main inventory", "material": stock.material_name, "weight_kg": stock.weight_kg}


@router.put("/reusable/{stock_id}/mark-scrap")
def mark_reusable_as_scrap(
    stock_id: int,
    reason: str = "unusable",
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Mark reusable stock as scrap (when it can't be used)"""
    stock = db.query(models.ReusableStock).filter(models.ReusableStock.id == stock_id).first()
    if not stock:
        raise HTTPException(status_code=404, detail="Not found")
    
    scrap = models.ScrapRecord(
        material_name=stock.material_name,
        weight_kg=stock.weight_kg,
        length_mm=stock.length_mm,
        width_mm=stock.width_mm,
        quantity=stock.quantity,
        reason_code=reason,
        dimensions=stock.dimensions,
        status="pending",
        created_by=current_user.id,
    )
    db.add(scrap)
    stock.is_available = False
    db.commit()
    return {"message": "Moved to scrap", "scrap_id": scrap.id}


@router.delete("/reusable/{stock_id}")
def delete_reusable_stock(
    stock_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("Boss", "Software Supervisor")),
):
    """Delete reusable stock item"""
    stock = db.query(models.ReusableStock).filter(models.ReusableStock.id == stock_id).first()
    if not stock:
        raise HTTPException(status_code=404, detail="Not found")
    
    db.delete(stock)
    db.commit()
    return {"message": "Deleted", "id": stock_id}


# ============ Analytics Endpoints ============

@router.get("/analytics")
def get_loss_analytics(
    days: int = Query(30),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get loss analytics and KPIs for dashboard"""
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    # Inventory totals
    inv = db.query(models.Inventory).all()
    total_input = sum(float(i.total or 0) for i in inv)
    total_consumed = sum(float(i.used or 0) for i in inv)
    
    # Scrap totals
    scrap = db.query(models.ScrapRecord).filter(
        models.ScrapRecord.created_at >= cutoff
    ).all()
    total_scrap = sum(float(s.weight_kg or 0) for s in scrap)
    
    # Scrap by reason
    scrap_by_reason = {}
    for s in scrap:
        reason = s.reason_code or "other"
        scrap_by_reason[reason] = scrap_by_reason.get(reason, 0) + float(s.weight_kg or 0)
    
    # Scrap by material
    scrap_by_material = {}
    for s in scrap:
        mat = s.material_name or "Unknown"
        scrap_by_material[mat] = scrap_by_material.get(mat, 0) + float(s.weight_kg or 0)
    
    # Reusable totals
    reusable = db.query(models.ReusableStock).filter(
        models.ReusableStock.is_available == True
    ).all()
    total_reusable = sum(float(r.weight_kg or 0) for r in reusable)
    
    # Calculate rates
    scrap_rate = (total_scrap / total_consumed * 100) if total_consumed > 0 else 0
    recovery_rate = (total_reusable / total_scrap * 100) if total_scrap > 0 else 0
    
    # Estimated loss value
    estimated_loss = total_scrap * 50  # 50 per kg default rate
    
    return {
        "period_days": days,
        "total_input_kg": round(total_input, 2),
        "total_consumed_kg": round(total_consumed, 2),
        "total_scrap_kg": round(total_scrap, 2),
        "total_reusable_kg": round(total_reusable, 2),
        "scrap_rate_pct": round(scrap_rate, 2),
        "recovery_rate_pct": round(recovery_rate, 2),
        "scrap_by_reason": scrap_by_reason,
        "scrap_by_material": scrap_by_material,
        "estimated_loss_value": round(estimated_loss, 2),
    }


@router.get("/summary")
def get_scrap_summary(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Quick summary for dashboard widgets"""
    scrap_total = db.query(func.sum(models.ScrapRecord.weight_kg)).scalar() or 0
    scrap_pending = db.query(func.sum(models.ScrapRecord.weight_kg)).filter(
        models.ScrapRecord.status == "pending"
    ).scalar() or 0
    scrap_count = db.query(models.ScrapRecord).count()
    
    reusable_available = db.query(func.sum(models.ReusableStock.weight_kg)).filter(
        models.ReusableStock.is_available == True
    ).scalar() or 0
    reusable_count = db.query(models.ReusableStock).filter(
        models.ReusableStock.is_available == True
    ).count()
    
    # Recent scrap (last 7 days)
    week_ago = datetime.utcnow() - timedelta(days=7)
    recent_scrap = db.query(func.sum(models.ScrapRecord.weight_kg)).filter(
        models.ScrapRecord.created_at >= week_ago
    ).scalar() or 0
    
    return {
        "scrap_total_kg": round(float(scrap_total), 2),
        "scrap_pending_kg": round(float(scrap_pending), 2),
        "scrap_records_count": scrap_count,
        "reusable_available_kg": round(float(reusable_available), 2),
        "reusable_items_count": reusable_count,
        "recent_scrap_kg": round(float(recent_scrap), 2),
    }


# ============ Bulk Actions ============

@router.post("/bulk-action")
def bulk_scrap_action(
    action: str,  # return_to_inventory, dispose, mark_reusable
    record_ids: List[int],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("Boss", "Software Supervisor", "Store Keeper")),
):
    """Perform bulk action on multiple scrap records"""
    records = db.query(models.ScrapRecord).filter(
        models.ScrapRecord.id.in_(record_ids)
    ).all()
    
    if not records:
        raise HTTPException(status_code=404, detail="No records found")
    
    results = []
    
    for record in records:
        if action == "return_to_inventory":
            inv = db.query(models.Inventory).filter(
                models.Inventory.name.ilike(f"%{record.material_name}%")
            ).first()
            if inv:
                inv.total = (inv.total or 0) + record.weight_kg
            else:
                inv = models.Inventory(
                    name=record.material_name,
                    unit="kg",
                    total=record.weight_kg,
                    used=0,
                    category="reusable",
                )
                db.add(inv)
            record.status = "returned_to_inventory"
            results.append({"id": record.id, "action": "returned"})
            
        elif action == "dispose":
            record.status = "disposed"
            results.append({"id": record.id, "action": "disposed"})
            
        elif action == "mark_reusable":
            reusable = models.ReusableStock(
                material_name=record.material_name,
                dimensions=record.dimensions or "",
                weight_kg=record.weight_kg,
                length_mm=record.length_mm,
                width_mm=record.width_mm,
                quantity=record.quantity,
                quality_grade="B",
                is_available=True,
                created_by=current_user.id,
            )
            db.add(reusable)
            record.status = "returned_to_inventory"
            results.append({"id": record.id, "action": "moved_to_reusable"})
    
    db.commit()
    return {"message": f"Processed {len(results)} records", "results": results}
