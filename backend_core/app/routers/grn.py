"""
GRN (Goods Receipt Note) API Router
====================================
Complete GRN workflow for steel industry inward operations:
- Gate entry
- Weighbridge integration
- QA inspection
- Stock creation

Author: System Architect Review
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..security import (
    get_db, get_current_user, require_permission, Permission,
    SecurityAuditLog
)
from ..models_v2 import (
    GoodsReceiptNote, GRNLineItem, MaterialMaster, Vendor,
    StockLot, DocumentStatus, QAStatus, WeightUnit
)
from ..services.inventory_service import (
    GRNService, StockLotService, get_next_sequence
)
from ..models import Customer

router = APIRouter(prefix="/api/v2/grn", tags=["GRN - Goods Receipt"])


# =============================================================================
# SCHEMAS
# =============================================================================

class GRNCreateRequest(BaseModel):
    """Request to create a new GRN"""
    vendor_id: int
    vendor_invoice_number: Optional[str] = None
    vendor_invoice_date: Optional[datetime] = None
    vehicle_number: Optional[str] = None
    driver_name: Optional[str] = None
    driver_contact: Optional[str] = None
    remarks: Optional[str] = None


class GRNLineItemCreate(BaseModel):
    """Request to add a line item to GRN"""
    material_id: int
    heat_number: Optional[str] = None
    batch_number: Optional[str] = None
    ordered_qty: Optional[float] = None
    received_qty: float = Field(..., gt=0)
    weight_kg: float = Field(..., gt=0)
    unit: str = "kg"
    rate: Optional[float] = None
    
    @validator('weight_kg', 'received_qty')
    def round_precision(cls, v):
        return round(v, 3)


class WeighmentData(BaseModel):
    """Weighbridge data for GRN"""
    gross_weight_kg: float = Field(..., gt=0)
    tare_weight_kg: float = Field(..., ge=0)
    weighbridge_slip_number: Optional[str] = None
    
    @property
    def net_weight_kg(self) -> float:
        return self.gross_weight_kg - self.tare_weight_kg


class QAInspectionResult(BaseModel):
    """QA inspection result for a GRN line item"""
    line_item_id: int
    status: str  # approved, rejected, conditional, on_hold
    accepted_qty: Optional[float] = None
    rejected_qty: Optional[float] = None
    remarks: Optional[str] = None
    test_certificate_ref: Optional[str] = None


class GRNLineItemOut(BaseModel):
    """Output schema for GRN line item"""
    id: int
    material_code: Optional[str] = None
    material_name: Optional[str] = None
    heat_number: Optional[str]
    batch_number: Optional[str]
    ordered_qty: Optional[float]
    received_qty: float
    accepted_qty: Optional[float]
    rejected_qty: float
    weight_kg: float
    unit: str
    rate: Optional[float]
    amount: Optional[float]
    qa_status: str

    class Config:
        from_attributes = True


class GRNOut(BaseModel):
    """Output schema for GRN"""
    id: int
    grn_number: str
    vendor_name: Optional[str] = None
    vendor_invoice_number: Optional[str]
    vehicle_number: Optional[str]
    driver_name: Optional[str]
    gross_weight_kg: Optional[float]
    tare_weight_kg: Optional[float]
    net_weight_kg: Optional[float]
    status: str
    gate_entry_time: Optional[datetime]
    weighment_time: Optional[datetime]
    received_time: Optional[datetime]
    created_at: datetime
    line_items: List[GRNLineItemOut] = []

    class Config:
        from_attributes = True


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("/", response_model=List[GRNOut])
async def list_grns(
    status: Optional[str] = None,
    vendor_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.GRN_VIEW))
):
    """
    List all GRNs with optional filtering.
    
    Status values: draft, submitted, approved, cancelled
    """
    query = db.query(
        GoodsReceiptNote,
        Vendor.name.label('vendor_name')
    ).join(
        Vendor, GoodsReceiptNote.vendor_id == Vendor.id
    )
    
    if status:
        query = query.filter(GoodsReceiptNote.status == DocumentStatus(status))
    
    if vendor_id:
        query = query.filter(GoodsReceiptNote.vendor_id == vendor_id)
    
    if date_from:
        query = query.filter(GoodsReceiptNote.created_at >= date_from)
    
    if date_to:
        query = query.filter(GoodsReceiptNote.created_at <= date_to)
    
    results = query.order_by(
        GoodsReceiptNote.created_at.desc()
    ).offset(offset).limit(limit).all()
    
    output = []
    for grn, vendor_name in results:
        # Get line items
        line_items = []
        for line in grn.line_items:
            material = db.query(MaterialMaster).filter(
                MaterialMaster.id == line.material_id
            ).first()
            
            line_items.append(GRNLineItemOut(
                id=line.id,
                material_code=material.code if material else None,
                material_name=material.name if material else None,
                heat_number=line.heat_number,
                batch_number=line.batch_number,
                ordered_qty=float(line.ordered_qty) if line.ordered_qty else None,
                received_qty=float(line.received_qty),
                accepted_qty=float(line.accepted_qty) if line.accepted_qty else None,
                rejected_qty=float(line.rejected_qty) if line.rejected_qty else 0,
                weight_kg=float(line.weight_kg),
                unit=line.unit.value if line.unit else "kg",
                rate=float(line.rate) if line.rate else None,
                amount=float(line.amount) if line.amount else None,
                qa_status=line.qa_status.value if line.qa_status else "pending"
            ))
        
        output.append(GRNOut(
            id=grn.id,
            grn_number=grn.grn_number,
            vendor_name=vendor_name,
            vendor_invoice_number=grn.vendor_invoice_number,
            vehicle_number=grn.vehicle_number,
            driver_name=grn.driver_name,
            gross_weight_kg=float(grn.gross_weight_kg) if grn.gross_weight_kg else None,
            tare_weight_kg=float(grn.tare_weight_kg) if grn.tare_weight_kg else None,
            net_weight_kg=float(grn.net_weight_kg) if grn.net_weight_kg else None,
            status=grn.status.value if grn.status else "draft",
            gate_entry_time=grn.gate_entry_time,
            weighment_time=grn.weighment_time,
            received_time=grn.received_time,
            created_at=grn.created_at,
            line_items=line_items
        ))
    
    return output


@router.post("/", status_code=201)
async def create_grn(
    data: GRNCreateRequest,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.GRN_CREATE))
):
    """
    Create a new GRN in draft status.
    
    This is typically done at gate entry when a vehicle arrives.
    """
    # Validate vendor exists
    vendor = db.query(Vendor).filter(Vendor.id == data.vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    
    grn = GRNService.create_grn(
        db=db,
        vendor_id=data.vendor_id,
        user_id=current_user.id,
        vehicle_number=data.vehicle_number,
        vendor_invoice_number=data.vendor_invoice_number
    )
    
    if data.driver_name:
        grn.driver_name = data.driver_name
    if data.driver_contact:
        grn.driver_contact = data.driver_contact
    if data.vendor_invoice_date:
        grn.vendor_invoice_date = data.vendor_invoice_date
    if data.remarks:
        grn.remarks = data.remarks
    
    db.commit()
    db.refresh(grn)
    
    SecurityAuditLog.log_sensitive_action(
        db, current_user.id, "create", "grn",
        grn.id, {"grn_number": grn.grn_number, "vendor_id": data.vendor_id}
    )
    
    return {
        "success": True,
        "grn_id": grn.id,
        "grn_number": grn.grn_number,
        "status": grn.status.value
    }


@router.post("/{grn_id}/line-items")
async def add_grn_line_item(
    grn_id: int,
    data: GRNLineItemCreate,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.GRN_CREATE))
):
    """
    Add a line item to a GRN.
    
    Line items represent individual materials received.
    For steel industry, always record heat_number for traceability.
    """
    line = GRNService.add_line_item(
        db=db,
        grn_id=grn_id,
        material_id=data.material_id,
        received_qty=Decimal(str(data.received_qty)),
        weight_kg=Decimal(str(data.weight_kg)),
        unit=WeightUnit(data.unit),
        heat_number=data.heat_number,
        batch_number=data.batch_number,
        rate=Decimal(str(data.rate)) if data.rate else None
    )
    
    if data.ordered_qty:
        line.ordered_qty = Decimal(str(data.ordered_qty))
    
    db.commit()
    db.refresh(line)
    
    return {
        "success": True,
        "line_item_id": line.id,
        "message": "Line item added to GRN"
    }


@router.post("/{grn_id}/weighment")
async def record_weighment(
    grn_id: int,
    data: WeighmentData,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.GRN_CREATE))
):
    """
    Record weighbridge data for a GRN.
    
    This captures the gross and tare weights from the weighbridge.
    Net weight is automatically calculated.
    """
    grn = db.query(GoodsReceiptNote).filter(
        GoodsReceiptNote.id == grn_id
    ).first()
    
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")
    
    if grn.status != DocumentStatus.DRAFT:
        raise HTTPException(
            status_code=400,
            detail="Weighment can only be recorded for draft GRNs"
        )
    
    grn.gross_weight_kg = Decimal(str(data.gross_weight_kg))
    grn.tare_weight_kg = Decimal(str(data.tare_weight_kg))
    grn.net_weight_kg = grn.gross_weight_kg - grn.tare_weight_kg
    grn.weighbridge_slip_number = data.weighbridge_slip_number
    grn.weighment_time = datetime.utcnow()
    
    db.commit()
    
    return {
        "success": True,
        "gross_weight_kg": float(grn.gross_weight_kg),
        "tare_weight_kg": float(grn.tare_weight_kg),
        "net_weight_kg": float(grn.net_weight_kg)
    }


@router.post("/{grn_id}/submit")
async def submit_grn(
    grn_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.GRN_CREATE))
):
    """
    Submit GRN for QA inspection and approval.
    
    Validates:
    - GRN has at least one line item
    - Weighment data is recorded
    """
    grn = db.query(GoodsReceiptNote).filter(
        GoodsReceiptNote.id == grn_id
    ).first()
    
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")
    
    if grn.status != DocumentStatus.DRAFT:
        raise HTTPException(
            status_code=400,
            detail=f"GRN is already {grn.status.value}"
        )
    
    if not grn.line_items:
        raise HTTPException(
            status_code=400,
            detail="GRN must have at least one line item"
        )
    
    if not grn.net_weight_kg:
        raise HTTPException(
            status_code=400,
            detail="Weighment data must be recorded before submission"
        )
    
    grn.status = DocumentStatus.SUBMITTED
    grn.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {
        "success": True,
        "message": "GRN submitted for approval",
        "status": grn.status.value
    }


@router.post("/{grn_id}/qa-inspection")
async def record_qa_inspection(
    grn_id: int,
    inspections: List[QAInspectionResult],
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.QA_INSPECT))
):
    """
    Record QA inspection results for GRN line items.
    
    Each line item must be inspected and given a status:
    - approved: Material meets quality standards
    - rejected: Material does not meet standards
    - conditional: Accepted with conditions/deviations
    - on_hold: Needs further testing
    """
    grn = db.query(GoodsReceiptNote).filter(
        GoodsReceiptNote.id == grn_id
    ).first()
    
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")
    
    if grn.status != DocumentStatus.SUBMITTED:
        raise HTTPException(
            status_code=400,
            detail="GRN must be submitted before QA inspection"
        )
    
    results = []
    
    for inspection in inspections:
        line = db.query(GRNLineItem).filter(
            GRNLineItem.id == inspection.line_item_id,
            GRNLineItem.grn_id == grn_id
        ).first()
        
        if not line:
            results.append({
                "line_item_id": inspection.line_item_id,
                "success": False,
                "error": "Line item not found"
            })
            continue
        
        line.qa_status = QAStatus(inspection.status)
        line.qa_remarks = inspection.remarks
        
        if inspection.accepted_qty is not None:
            line.accepted_qty = Decimal(str(inspection.accepted_qty))
        if inspection.rejected_qty is not None:
            line.rejected_qty = Decimal(str(inspection.rejected_qty))
        
        results.append({
            "line_item_id": inspection.line_item_id,
            "success": True,
            "status": inspection.status
        })
    
    db.commit()
    
    return {
        "success": True,
        "results": results
    }


@router.post("/{grn_id}/approve")
async def approve_grn(
    grn_id: int,
    location_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.GRN_APPROVE))
):
    """
    Approve GRN and create stock lots.
    
    This is a critical operation that:
    1. Validates all line items have QA decision
    2. Creates stock lots for approved/conditional items
    3. Records stock movements
    4. Updates GRN status
    
    location_id: Storage location where material will be stored
    """
    try:
        grn, lots = GRNService.approve_grn(
            db=db,
            grn_id=grn_id,
            user_id=current_user.id,
            location_id=location_id
        )
        
        db.commit()
        
        SecurityAuditLog.log_sensitive_action(
            db, current_user.id, "approve", "grn",
            grn.id, {
                "grn_number": grn.grn_number,
                "lots_created": len(lots),
                "lot_numbers": [l.lot_number for l in lots]
            }
        )
        
        return {
            "success": True,
            "message": f"GRN approved. Created {len(lots)} stock lots.",
            "grn_number": grn.grn_number,
            "lots_created": [
                {
                    "lot_number": lot.lot_number,
                    "material_id": lot.material_id,
                    "weight_kg": float(lot.net_weight_kg)
                }
                for lot in lots
            ]
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{grn_id}/cancel")
async def cancel_grn(
    grn_id: int,
    reason: str,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.GRN_APPROVE))
):
    """
    Cancel a GRN.
    
    Only draft or submitted GRNs can be cancelled.
    Approved GRNs with created stock cannot be cancelled (use returns instead).
    """
    grn = db.query(GoodsReceiptNote).filter(
        GoodsReceiptNote.id == grn_id
    ).first()
    
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")
    
    if grn.status == DocumentStatus.APPROVED:
        raise HTTPException(
            status_code=400,
            detail="Approved GRNs cannot be cancelled. Use return process instead."
        )
    
    if grn.status == DocumentStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="GRN is already cancelled")
    
    grn.status = DocumentStatus.CANCELLED
    grn.remarks = f"{grn.remarks or ''}\n[CANCELLED] {reason}".strip()
    grn.updated_at = datetime.utcnow()
    
    db.commit()
    
    SecurityAuditLog.log_sensitive_action(
        db, current_user.id, "cancel", "grn",
        grn.id, {"grn_number": grn.grn_number, "reason": reason}
    )
    
    return {
        "success": True,
        "message": "GRN cancelled",
        "grn_number": grn.grn_number
    }
