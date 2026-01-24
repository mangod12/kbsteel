"""
Dispatch API Router
===================
Complete dispatch workflow for steel industry outward operations:
- Dispatch request creation
- Material picking (FIFO)
- Weighbridge verification
- Dispatch approval and stock deduction

Author: System Architect Review
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from ..security import (
    get_db, get_current_user, require_permission, Permission,
    SecurityAuditLog
)
from ..models_v2 import (
    DispatchNote, DispatchLineItem, StockLot, MaterialMaster,
    DocumentStatus, QAStatus, MovementType
)
from ..models import Customer
from ..services.inventory_service import (
    StockLotService, InventoryQueryService, get_next_sequence,
    InsufficientStockError, kg_to_tons
)

router = APIRouter(prefix="/api/v2/dispatch", tags=["Dispatch"])


# =============================================================================
# SCHEMAS
# =============================================================================

class DispatchCreateRequest(BaseModel):
    """Request to create a dispatch note"""
    customer_id: int
    sales_order_ref: Optional[str] = None
    vehicle_number: Optional[str] = None
    transporter: Optional[str] = None
    driver_name: Optional[str] = None
    driver_contact: Optional[str] = None
    remarks: Optional[str] = None


class DispatchLineItemCreate(BaseModel):
    """Request to add item to dispatch"""
    stock_lot_id: int
    weight_kg: float = Field(..., gt=0)
    rate: Optional[float] = None
    
    @validator('weight_kg')
    def round_weight(cls, v):
        return round(v, 3)


class AutoPickRequest(BaseModel):
    """Request for automatic FIFO picking"""
    material_id: int
    required_weight_kg: float = Field(..., gt=0)
    location_id: Optional[int] = None


class DispatchWeighmentData(BaseModel):
    """Weighbridge data for dispatch verification"""
    gross_weight_kg: float = Field(..., gt=0)
    tare_weight_kg: float = Field(..., ge=0)


class DispatchLineItemOut(BaseModel):
    """Output schema for dispatch line item"""
    id: int
    lot_number: str
    material_code: Optional[str] = None
    material_name: Optional[str] = None
    heat_number: Optional[str] = None
    dispatched_weight_kg: float
    rate: Optional[float]
    amount: Optional[float]

    class Config:
        from_attributes = True


class DispatchOut(BaseModel):
    """Output schema for dispatch note"""
    id: int
    dispatch_number: str
    customer_name: Optional[str] = None
    sales_order_ref: Optional[str]
    vehicle_number: Optional[str]
    transporter: Optional[str]
    driver_name: Optional[str]
    gross_weight_kg: Optional[float]
    tare_weight_kg: Optional[float]
    net_weight_kg: Optional[float]
    status: str
    requested_at: datetime
    dispatched_at: Optional[datetime]
    line_items: List[DispatchLineItemOut] = []

    class Config:
        from_attributes = True


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("/", response_model=List[DispatchOut])
async def list_dispatches(
    status: Optional[str] = None,
    customer_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.DISPATCH_VIEW))
):
    """
    List all dispatch notes with optional filtering.
    """
    query = db.query(
        DispatchNote,
        Customer.name.label('customer_name')
    ).join(
        Customer, DispatchNote.customer_id == Customer.id
    )
    
    if status:
        query = query.filter(DispatchNote.status == DocumentStatus(status))
    
    if customer_id:
        query = query.filter(DispatchNote.customer_id == customer_id)
    
    if date_from:
        query = query.filter(DispatchNote.created_at >= date_from)
    
    if date_to:
        query = query.filter(DispatchNote.created_at <= date_to)
    
    results = query.order_by(
        DispatchNote.created_at.desc()
    ).offset(offset).limit(limit).all()
    
    output = []
    for dispatch, customer_name in results:
        line_items = []
        for line in dispatch.line_items:
            lot = db.query(StockLot).filter(StockLot.id == line.stock_lot_id).first()
            material = db.query(MaterialMaster).filter(
                MaterialMaster.id == lot.material_id
            ).first() if lot else None
            
            line_items.append(DispatchLineItemOut(
                id=line.id,
                lot_number=lot.lot_number if lot else "N/A",
                material_code=material.code if material else None,
                material_name=material.name if material else None,
                heat_number=lot.heat_number if lot else None,
                dispatched_weight_kg=float(line.dispatched_weight_kg),
                rate=float(line.rate) if line.rate else None,
                amount=float(line.amount) if line.amount else None
            ))
        
        output.append(DispatchOut(
            id=dispatch.id,
            dispatch_number=dispatch.dispatch_number,
            customer_name=customer_name,
            sales_order_ref=dispatch.sales_order_ref,
            vehicle_number=dispatch.vehicle_number,
            transporter=dispatch.transporter,
            driver_name=dispatch.driver_name,
            gross_weight_kg=float(dispatch.gross_weight_kg) if dispatch.gross_weight_kg else None,
            tare_weight_kg=float(dispatch.tare_weight_kg) if dispatch.tare_weight_kg else None,
            net_weight_kg=float(dispatch.net_weight_kg) if dispatch.net_weight_kg else None,
            status=dispatch.status.value if dispatch.status else "draft",
            requested_at=dispatch.requested_at,
            dispatched_at=dispatch.dispatched_at,
            line_items=line_items
        ))
    
    return output


@router.post("/", status_code=201)
async def create_dispatch(
    data: DispatchCreateRequest,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.DISPATCH_CREATE))
):
    """
    Create a new dispatch note.
    """
    # Validate customer exists
    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    dispatch = DispatchNote(
        dispatch_number=get_next_sequence(db, "dispatch", "DSP"),
        customer_id=data.customer_id,
        sales_order_ref=data.sales_order_ref,
        vehicle_number=data.vehicle_number,
        transporter=data.transporter,
        driver_name=data.driver_name,
        driver_contact=data.driver_contact,
        remarks=data.remarks,
        status=DocumentStatus.DRAFT,
        created_by=current_user.id
    )
    
    db.add(dispatch)
    db.commit()
    db.refresh(dispatch)
    
    return {
        "success": True,
        "dispatch_id": dispatch.id,
        "dispatch_number": dispatch.dispatch_number
    }


@router.post("/{dispatch_id}/line-items")
async def add_dispatch_line_item(
    dispatch_id: int,
    data: DispatchLineItemCreate,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.DISPATCH_CREATE))
):
    """
    Add a specific lot to the dispatch.
    
    Validates:
    - Lot exists and is active
    - Lot has sufficient stock
    - Lot is QA approved
    """
    dispatch = db.query(DispatchNote).filter(
        DispatchNote.id == dispatch_id
    ).first()
    
    if not dispatch:
        raise HTTPException(status_code=404, detail="Dispatch not found")
    
    if dispatch.status != DocumentStatus.DRAFT:
        raise HTTPException(
            status_code=400,
            detail="Can only add items to draft dispatches"
        )
    
    # Validate lot
    lot = db.query(StockLot).filter(StockLot.id == data.stock_lot_id).first()
    
    if not lot:
        raise HTTPException(status_code=404, detail="Stock lot not found")
    
    if not lot.is_active:
        raise HTTPException(status_code=400, detail="Stock lot is not active")
    
    if lot.is_blocked:
        raise HTTPException(
            status_code=400,
            detail=f"Stock lot is blocked: {lot.block_reason}"
        )
    
    if lot.qa_status not in (QAStatus.APPROVED, QAStatus.CONDITIONAL):
        raise HTTPException(
            status_code=400,
            detail="Stock lot is not QA approved"
        )
    
    weight_kg = Decimal(str(data.weight_kg))
    
    # Check if lot already in this dispatch
    existing = db.query(DispatchLineItem).filter(
        DispatchLineItem.dispatch_id == dispatch_id,
        DispatchLineItem.stock_lot_id == data.stock_lot_id
    ).first()
    
    if existing:
        # Update existing line
        total_weight = existing.dispatched_weight_kg + weight_kg
        if total_weight > lot.current_weight_kg:
            raise HTTPException(
                status_code=400,
                detail=f"Total dispatch weight ({total_weight}) exceeds available ({lot.current_weight_kg})"
            )
        existing.dispatched_weight_kg = total_weight
        if data.rate:
            existing.rate = Decimal(str(data.rate))
            existing.amount = existing.dispatched_weight_kg * existing.rate
        db.commit()
        return {
            "success": True,
            "message": "Updated existing line item",
            "line_item_id": existing.id
        }
    
    # Check available stock
    if weight_kg > lot.current_weight_kg:
        raise HTTPException(
            status_code=400,
            detail=f"Requested weight ({weight_kg}) exceeds available ({lot.current_weight_kg})"
        )
    
    rate = Decimal(str(data.rate)) if data.rate else None
    amount = (rate * weight_kg) if rate else None
    
    line = DispatchLineItem(
        dispatch_id=dispatch_id,
        stock_lot_id=data.stock_lot_id,
        dispatched_weight_kg=weight_kg,
        rate=rate,
        amount=amount
    )
    
    db.add(line)
    db.commit()
    db.refresh(line)
    
    return {
        "success": True,
        "line_item_id": line.id,
        "lot_number": lot.lot_number
    }


@router.post("/{dispatch_id}/auto-pick")
async def auto_pick_material(
    dispatch_id: int,
    data: AutoPickRequest,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.DISPATCH_CREATE))
):
    """
    Automatically pick material using FIFO (First In First Out).
    
    This finds the oldest available lots and adds them to the dispatch.
    Critical for:
    - Proper stock rotation
    - Preventing material aging
    - Quality compliance
    """
    dispatch = db.query(DispatchNote).filter(
        DispatchNote.id == dispatch_id
    ).first()
    
    if not dispatch:
        raise HTTPException(status_code=404, detail="Dispatch not found")
    
    if dispatch.status != DocumentStatus.DRAFT:
        raise HTTPException(
            status_code=400,
            detail="Can only add items to draft dispatches"
        )
    
    try:
        picks = InventoryQueryService.get_lots_for_fifo_pick(
            db=db,
            material_id=data.material_id,
            required_weight_kg=Decimal(str(data.required_weight_kg)),
            location_id=data.location_id
        )
    except InsufficientStockError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    added_lines = []
    
    for lot, weight_to_pick in picks:
        line = DispatchLineItem(
            dispatch_id=dispatch_id,
            stock_lot_id=lot.id,
            dispatched_weight_kg=weight_to_pick
        )
        db.add(line)
        db.flush()
        
        added_lines.append({
            "lot_number": lot.lot_number,
            "heat_number": lot.heat_number,
            "weight_kg": float(weight_to_pick),
            "lot_age_days": lot.age_days
        })
    
    db.commit()
    
    return {
        "success": True,
        "message": f"Auto-picked {len(picks)} lots using FIFO",
        "total_weight_kg": float(data.required_weight_kg),
        "picks": added_lines
    }


@router.post("/{dispatch_id}/weighment")
async def record_dispatch_weighment(
    dispatch_id: int,
    data: DispatchWeighmentData,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.DISPATCH_CREATE))
):
    """
    Record weighbridge data for dispatch verification.
    
    Compares actual weight with expected weight to detect discrepancies.
    """
    dispatch = db.query(DispatchNote).filter(
        DispatchNote.id == dispatch_id
    ).first()
    
    if not dispatch:
        raise HTTPException(status_code=404, detail="Dispatch not found")
    
    dispatch.gross_weight_kg = Decimal(str(data.gross_weight_kg))
    dispatch.tare_weight_kg = Decimal(str(data.tare_weight_kg))
    dispatch.net_weight_kg = dispatch.gross_weight_kg - dispatch.tare_weight_kg
    
    # Calculate expected weight from line items
    expected_weight = sum(
        line.dispatched_weight_kg for line in dispatch.line_items
    )
    
    variance = dispatch.net_weight_kg - expected_weight
    variance_pct = (variance / expected_weight * 100) if expected_weight > 0 else Decimal('0')
    
    db.commit()
    
    return {
        "success": True,
        "gross_weight_kg": float(dispatch.gross_weight_kg),
        "tare_weight_kg": float(dispatch.tare_weight_kg),
        "net_weight_kg": float(dispatch.net_weight_kg),
        "expected_weight_kg": float(expected_weight),
        "variance_kg": float(variance),
        "variance_percent": float(variance_pct),
        "within_tolerance": abs(variance_pct) <= Decimal('1')  # 1% tolerance
    }


@router.post("/{dispatch_id}/submit")
async def submit_dispatch(
    dispatch_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.DISPATCH_CREATE))
):
    """
    Submit dispatch for approval.
    """
    dispatch = db.query(DispatchNote).filter(
        DispatchNote.id == dispatch_id
    ).first()
    
    if not dispatch:
        raise HTTPException(status_code=404, detail="Dispatch not found")
    
    if dispatch.status != DocumentStatus.DRAFT:
        raise HTTPException(
            status_code=400,
            detail=f"Dispatch is already {dispatch.status.value}"
        )
    
    if not dispatch.line_items:
        raise HTTPException(
            status_code=400,
            detail="Dispatch must have at least one item"
        )
    
    dispatch.status = DocumentStatus.SUBMITTED
    dispatch.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {
        "success": True,
        "message": "Dispatch submitted for approval",
        "dispatch_number": dispatch.dispatch_number
    }


@router.post("/{dispatch_id}/approve")
async def approve_dispatch(
    dispatch_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.DISPATCH_APPROVE))
):
    """
    Approve dispatch and deduct stock.
    
    This is a critical operation that:
    1. Validates all line items have sufficient stock
    2. Deducts stock from each lot
    3. Creates stock movement records
    4. Updates dispatch status
    """
    dispatch = db.query(DispatchNote).filter(
        DispatchNote.id == dispatch_id
    ).with_for_update().first()
    
    if not dispatch:
        raise HTTPException(status_code=404, detail="Dispatch not found")
    
    if dispatch.status != DocumentStatus.SUBMITTED:
        raise HTTPException(
            status_code=400,
            detail="Dispatch must be submitted before approval"
        )
    
    # Process each line item
    movements = []
    
    try:
        for line in dispatch.line_items:
            movement, lot = StockLotService.consume_from_lot(
                db=db,
                lot_id=line.stock_lot_id,
                weight_kg=line.dispatched_weight_kg,
                user_id=current_user.id,
                reason=f"Dispatch {dispatch.dispatch_number}",
                reference_type="dispatch",
                reference_id=dispatch.id
            )
            movements.append({
                "lot_number": lot.lot_number,
                "movement_number": movement.movement_number,
                "weight_dispatched": float(line.dispatched_weight_kg),
                "remaining_weight": float(lot.current_weight_kg)
            })
        
        dispatch.status = DocumentStatus.APPROVED
        dispatch.approved_by = current_user.id
        dispatch.dispatched_at = datetime.utcnow()
        dispatch.updated_at = datetime.utcnow()
        
        db.commit()
        
        SecurityAuditLog.log_sensitive_action(
            db, current_user.id, "approve", "dispatch",
            dispatch.id, {
                "dispatch_number": dispatch.dispatch_number,
                "total_weight": float(sum(m["weight_dispatched"] for m in movements)),
                "movements": len(movements)
            }
        )
        
        return {
            "success": True,
            "message": "Dispatch approved and stock deducted",
            "dispatch_number": dispatch.dispatch_number,
            "movements": movements
        }
        
    except InsufficientStockError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{dispatch_id}/line-items/{line_item_id}")
async def remove_dispatch_line_item(
    dispatch_id: int,
    line_item_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.DISPATCH_CREATE))
):
    """
    Remove a line item from a dispatch.
    
    Only works for draft dispatches.
    """
    dispatch = db.query(DispatchNote).filter(
        DispatchNote.id == dispatch_id
    ).first()
    
    if not dispatch:
        raise HTTPException(status_code=404, detail="Dispatch not found")
    
    if dispatch.status != DocumentStatus.DRAFT:
        raise HTTPException(
            status_code=400,
            detail="Can only modify draft dispatches"
        )
    
    line = db.query(DispatchLineItem).filter(
        DispatchLineItem.id == line_item_id,
        DispatchLineItem.dispatch_id == dispatch_id
    ).first()
    
    if not line:
        raise HTTPException(status_code=404, detail="Line item not found")
    
    db.delete(line)
    db.commit()
    
    return {"success": True, "message": "Line item removed"}
