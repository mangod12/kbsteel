"""
Improved Inventory API Router
=============================
Proper REST API with:
- Transaction safety
- Audit logging
- Proper error handling
- Steel industry specific operations

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
    StockLot, MaterialMaster, StorageLocation, StockMovement,
    QAStatus, WeightUnit, MovementType
)
from ..services.inventory_service import (
    StockLotService, InventoryQueryService, GRNService,
    InsufficientStockError, InvalidOperationError, WeightMismatchError,
    kg_to_tons, normalize_weight
)

router = APIRouter(prefix="/api/v2/inventory", tags=["Inventory v2"])


# =============================================================================
# PYDANTIC SCHEMAS
# =============================================================================

class MaterialMasterCreate(BaseModel):
    """Schema for creating a new material master entry"""
    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=200)
    material_type: str
    grade: Optional[str] = None
    specification: Optional[str] = None
    thickness_mm: Optional[float] = None
    width_mm: Optional[float] = None
    length_mm: Optional[float] = None
    diameter_mm: Optional[float] = None
    default_unit: str = "kg"
    reorder_level: float = 0
    category: Optional[str] = None
    sub_category: Optional[str] = None
    hsn_code: Optional[str] = None


class MaterialMasterOut(BaseModel):
    """Schema for material master output"""
    id: int
    code: str
    name: str
    material_type: str
    grade: Optional[str]
    specification: Optional[str]
    thickness_mm: Optional[float]
    width_mm: Optional[float]
    default_unit: str
    reorder_level: float
    category: Optional[str]
    hsn_code: Optional[str]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class StockLotOut(BaseModel):
    """Schema for stock lot output"""
    id: int
    lot_number: str
    material_code: Optional[str] = None
    material_name: Optional[str] = None
    heat_number: Optional[str]
    batch_number: Optional[str]
    gross_weight_kg: float
    tare_weight_kg: float
    net_weight_kg: float
    current_weight_kg: float
    current_weight_tons: float
    unit: str
    qa_status: str
    location_code: Optional[str] = None
    received_date: datetime
    age_days: int
    is_low_stock: bool
    is_active: bool

    class Config:
        from_attributes = True


class StockSummaryOut(BaseModel):
    """Aggregated stock summary by material"""
    material_id: int
    material_code: str
    material_name: str
    material_type: str
    grade: Optional[str]
    lot_count: int
    total_weight_kg: float
    total_weight_tons: float
    oldest_lot_date: Optional[datetime]
    newest_lot_date: Optional[datetime]


class StockMovementOut(BaseModel):
    """Stock movement audit trail entry"""
    id: int
    movement_number: str
    lot_number: str
    movement_type: str
    weight_change_kg: float
    weight_before_kg: float
    weight_after_kg: float
    reason: Optional[str]
    remarks: Optional[str]
    created_by_name: Optional[str]
    movement_date: datetime

    class Config:
        from_attributes = True


class ConsumeStockRequest(BaseModel):
    """Request to consume stock from a lot"""
    lot_id: int
    weight_kg: float = Field(..., gt=0, description="Weight to consume in KG")
    reason: str = Field(..., min_length=1)
    production_item_id: Optional[int] = None
    remarks: Optional[str] = None

    @validator('weight_kg')
    def validate_weight(cls, v):
        if v <= 0:
            raise ValueError("Weight must be positive")
        # Limit precision to 3 decimal places
        return round(v, 3)


class AdjustStockRequest(BaseModel):
    """Request to adjust stock quantity"""
    lot_id: int
    new_weight_kg: float = Field(..., ge=0)
    reason: str = Field(..., min_length=5)
    
    @validator('new_weight_kg')
    def validate_weight(cls, v):
        return round(v, 3)


class TransferLocationRequest(BaseModel):
    """Request to transfer lot to new location"""
    lot_id: int
    to_location_id: int
    reason: Optional[str] = None


class ReconciliationRequest(BaseModel):
    """Request for physical vs system reconciliation"""
    lot_id: int
    physical_weight_kg: float


# =============================================================================
# MATERIAL MASTER ENDPOINTS
# =============================================================================

@router.get("/materials", response_model=List[MaterialMasterOut])
async def list_materials(
    material_type: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.INVENTORY_VIEW))
):
    """
    List all materials in the master catalog.
    
    Use this to:
    - Browse available material types
    - Search for specific materials by code/name
    - Filter by category or type
    """
    query = db.query(MaterialMaster)
    
    if not include_inactive:
        query = query.filter(MaterialMaster.is_active == True)
    
    if material_type:
        query = query.filter(MaterialMaster.material_type == material_type)
    
    if category:
        query = query.filter(MaterialMaster.category == category)
    
    if search:
        search_filter = f"%{search}%"
        query = query.filter(
            (MaterialMaster.code.ilike(search_filter)) |
            (MaterialMaster.name.ilike(search_filter)) |
            (MaterialMaster.grade.ilike(search_filter))
        )
    
    return query.order_by(MaterialMaster.code).all()


@router.post("/materials", response_model=MaterialMasterOut, status_code=201)
async def create_material(
    data: MaterialMasterCreate,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.INVENTORY_CREATE))
):
    """
    Create a new material in the master catalog.
    
    This defines a material type, not actual stock.
    Stock is created via GRN process.
    """
    # Check for duplicate code
    existing = db.query(MaterialMaster).filter(
        MaterialMaster.code == data.code
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Material with code '{data.code}' already exists"
        )
    
    material = MaterialMaster(
        code=data.code,
        name=data.name,
        material_type=data.material_type,
        grade=data.grade,
        specification=data.specification,
        thickness_mm=Decimal(str(data.thickness_mm)) if data.thickness_mm else None,
        width_mm=Decimal(str(data.width_mm)) if data.width_mm else None,
        length_mm=Decimal(str(data.length_mm)) if data.length_mm else None,
        diameter_mm=Decimal(str(data.diameter_mm)) if data.diameter_mm else None,
        default_unit=WeightUnit(data.default_unit),
        reorder_level=Decimal(str(data.reorder_level)),
        category=data.category,
        sub_category=data.sub_category,
        hsn_code=data.hsn_code,
        is_active=True
    )
    
    db.add(material)
    db.commit()
    db.refresh(material)
    
    # Audit log
    SecurityAuditLog.log_sensitive_action(
        db, current_user.id, "create", "material_master",
        material.id, {"code": data.code, "name": data.name}
    )
    
    return material


# =============================================================================
# STOCK LOT ENDPOINTS
# =============================================================================

@router.get("/lots", response_model=List[StockLotOut])
async def list_stock_lots(
    material_id: Optional[int] = None,
    material_code: Optional[str] = None,
    heat_number: Optional[str] = None,
    location_id: Optional[int] = None,
    qa_status: Optional[str] = None,
    include_inactive: bool = False,
    low_stock_only: bool = False,
    limit: int = Query(100, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.INVENTORY_VIEW))
):
    """
    List stock lots with filtering.
    
    Key filters for steel industry:
    - heat_number: Critical for traceability
    - qa_status: Filter approved/pending/rejected stock
    - low_stock_only: Find lots below 15% remaining
    """
    query = db.query(
        StockLot,
        MaterialMaster.code.label('material_code'),
        MaterialMaster.name.label('material_name'),
        StorageLocation.code.label('location_code')
    ).join(
        MaterialMaster, StockLot.material_id == MaterialMaster.id
    ).outerjoin(
        StorageLocation, StockLot.location_id == StorageLocation.id
    )
    
    if not include_inactive:
        query = query.filter(StockLot.is_active == True)
    
    if material_id:
        query = query.filter(StockLot.material_id == material_id)
    
    if material_code:
        query = query.filter(MaterialMaster.code.ilike(f"%{material_code}%"))
    
    if heat_number:
        query = query.filter(StockLot.heat_number.ilike(f"%{heat_number}%"))
    
    if location_id:
        query = query.filter(StockLot.location_id == location_id)
    
    if qa_status:
        query = query.filter(StockLot.qa_status == QAStatus(qa_status))
    
    results = query.order_by(
        StockLot.received_date.asc()  # FIFO order
    ).offset(offset).limit(limit).all()
    
    output = []
    now = datetime.utcnow()
    
    for lot, mat_code, mat_name, loc_code in results:
        age_days = (now - lot.received_date).days
        is_low = float(lot.current_weight_kg / lot.net_weight_kg) < 0.15 if lot.net_weight_kg > 0 else False
        
        if low_stock_only and not is_low:
            continue
        
        output.append(StockLotOut(
            id=lot.id,
            lot_number=lot.lot_number,
            material_code=mat_code,
            material_name=mat_name,
            heat_number=lot.heat_number,
            batch_number=lot.batch_number,
            gross_weight_kg=float(lot.gross_weight_kg),
            tare_weight_kg=float(lot.tare_weight_kg),
            net_weight_kg=float(lot.net_weight_kg),
            current_weight_kg=float(lot.current_weight_kg),
            current_weight_tons=float(kg_to_tons(lot.current_weight_kg)),
            unit=lot.unit.value if lot.unit else "kg",
            qa_status=lot.qa_status.value if lot.qa_status else "pending",
            location_code=loc_code,
            received_date=lot.received_date,
            age_days=age_days,
            is_low_stock=is_low,
            is_active=lot.is_active
        ))
    
    return output


@router.get("/lots/{lot_id}", response_model=StockLotOut)
async def get_stock_lot(
    lot_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.INVENTORY_VIEW))
):
    """Get detailed information about a specific lot"""
    result = db.query(
        StockLot,
        MaterialMaster.code.label('material_code'),
        MaterialMaster.name.label('material_name'),
        StorageLocation.code.label('location_code')
    ).join(
        MaterialMaster, StockLot.material_id == MaterialMaster.id
    ).outerjoin(
        StorageLocation, StockLot.location_id == StorageLocation.id
    ).filter(
        StockLot.id == lot_id
    ).first()
    
    if not result:
        raise HTTPException(status_code=404, detail="Stock lot not found")
    
    lot, mat_code, mat_name, loc_code = result
    now = datetime.utcnow()
    age_days = (now - lot.received_date).days
    is_low = float(lot.current_weight_kg / lot.net_weight_kg) < 0.15 if lot.net_weight_kg > 0 else False
    
    return StockLotOut(
        id=lot.id,
        lot_number=lot.lot_number,
        material_code=mat_code,
        material_name=mat_name,
        heat_number=lot.heat_number,
        batch_number=lot.batch_number,
        gross_weight_kg=float(lot.gross_weight_kg),
        tare_weight_kg=float(lot.tare_weight_kg),
        net_weight_kg=float(lot.net_weight_kg),
        current_weight_kg=float(lot.current_weight_kg),
        current_weight_tons=float(kg_to_tons(lot.current_weight_kg)),
        unit=lot.unit.value if lot.unit else "kg",
        qa_status=lot.qa_status.value if lot.qa_status else "pending",
        location_code=loc_code,
        received_date=lot.received_date,
        age_days=age_days,
        is_low_stock=is_low,
        is_active=lot.is_active
    )


# =============================================================================
# STOCK OPERATIONS
# =============================================================================

@router.post("/consume")
async def consume_stock(
    request: ConsumeStockRequest,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.PRODUCTION_CONSUME))
):
    """
    Consume material from a stock lot.
    
    This is the primary operation for production material usage.
    Creates an immutable audit trail of the consumption.
    
    Validations:
    - Lot must be active and not blocked
    - Lot must be QA approved
    - Sufficient stock must be available
    """
    try:
        movement, lot = StockLotService.consume_from_lot(
            db=db,
            lot_id=request.lot_id,
            weight_kg=Decimal(str(request.weight_kg)),
            user_id=current_user.id,
            reason=request.reason,
            production_item_id=request.production_item_id
        )
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Consumed {request.weight_kg} kg from lot {lot.lot_number}",
            "movement_number": movement.movement_number,
            "remaining_weight_kg": float(lot.current_weight_kg),
            "remaining_weight_tons": float(kg_to_tons(lot.current_weight_kg))
        }
        
    except InsufficientStockError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except InvalidOperationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/adjust")
async def adjust_stock(
    request: AdjustStockRequest,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.INVENTORY_ADJUST))
):
    """
    Adjust stock quantity after physical verification.
    
    Use this for:
    - Reweighing corrections
    - Physical count reconciliation
    - Damage/wastage adjustments
    
    Note: Negative adjustments are logged for audit purposes.
    """
    try:
        # For negative adjustments, require additional approval
        # In real implementation, this would trigger an approval workflow
        approved_by = current_user.id  # Self-approval for now
        
        movement, lot = StockLotService.adjust_stock(
            db=db,
            lot_id=request.lot_id,
            new_weight_kg=Decimal(str(request.new_weight_kg)),
            user_id=current_user.id,
            reason=request.reason,
            approved_by=approved_by
        )
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Adjusted lot {lot.lot_number}",
            "movement_number": movement.movement_number,
            "weight_change_kg": float(movement.weight_change_kg),
            "new_weight_kg": float(lot.current_weight_kg)
        }
        
    except InvalidOperationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/transfer-location")
async def transfer_location(
    request: TransferLocationRequest,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.INVENTORY_UPDATE))
):
    """
    Transfer a lot to a different storage location.
    
    Use this for:
    - Moving material between yards/warehouses
    - Rack reorganization
    - Preparing for dispatch
    """
    try:
        movement, lot = StockLotService.transfer_location(
            db=db,
            lot_id=request.lot_id,
            to_location_id=request.to_location_id,
            user_id=current_user.id,
            reason=request.reason
        )
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Transferred lot {lot.lot_number}",
            "movement_number": movement.movement_number,
            "new_location_id": lot.location_id
        }
        
    except InvalidOperationError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# REPORTS & QUERIES
# =============================================================================

@router.get("/summary", response_model=List[StockSummaryOut])
async def get_stock_summary(
    material_id: Optional[int] = None,
    location_id: Optional[int] = None,
    qa_status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.REPORT_VIEW))
):
    """
    Get aggregated stock summary by material.
    
    This is the primary view for:
    - Dashboard stock overview
    - Reorder point checking
    - Capacity planning
    """
    qa = QAStatus(qa_status) if qa_status else None
    
    summary = InventoryQueryService.get_stock_summary(
        db=db,
        material_id=material_id,
        location_id=location_id,
        qa_status=qa
    )
    
    return [StockSummaryOut(**s) for s in summary]


@router.get("/aging-report")
async def get_aging_report(
    days_threshold: int = 90,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.REPORT_VIEW))
):
    """
    Get stock aging report for FIFO management.
    
    Critical for:
    - Identifying slow-moving stock
    - Preventing material degradation
    - FIFO compliance
    """
    report = InventoryQueryService.get_stock_aging_report(
        db=db,
        days_threshold=days_threshold
    )
    
    return {
        "threshold_days": days_threshold,
        "report_date": datetime.utcnow().isoformat(),
        "total_lots": len(report),
        "old_stock_lots": len([r for r in report if r['is_old_stock']]),
        "lots": report
    }


@router.post("/reconcile")
async def reconcile_stock(
    request: ReconciliationRequest,
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.INVENTORY_ADJUST))
):
    """
    Compare physical count with system record.
    
    Returns variance analysis and indicates if adjustment is needed.
    """
    result = InventoryQueryService.reconcile_physical_vs_system(
        db=db,
        lot_id=request.lot_id,
        physical_weight_kg=Decimal(str(request.physical_weight_kg))
    )
    
    return result


@router.get("/movements/{lot_id}", response_model=List[StockMovementOut])
async def get_lot_movements(
    lot_id: int,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.INVENTORY_VIEW))
):
    """
    Get movement history for a specific lot.
    
    Complete audit trail showing all changes to the lot.
    """
    from .. import models  # For User join
    
    movements = db.query(
        StockMovement,
        StockLot.lot_number,
        models.User.full_name.label('created_by_name')
    ).join(
        StockLot, StockMovement.stock_lot_id == StockLot.id
    ).outerjoin(
        models.User, StockMovement.created_by == models.User.id
    ).filter(
        StockMovement.stock_lot_id == lot_id
    ).order_by(
        StockMovement.movement_date.desc()
    ).limit(limit).all()
    
    return [
        StockMovementOut(
            id=m.id,
            movement_number=m.movement_number,
            lot_number=lot_num,
            movement_type=m.movement_type.value if m.movement_type else "",
            weight_change_kg=float(m.weight_change_kg),
            weight_before_kg=float(m.weight_before_kg),
            weight_after_kg=float(m.weight_after_kg),
            reason=m.reason,
            remarks=m.remarks,
            created_by_name=created_name,
            movement_date=m.movement_date
        )
        for m, lot_num, created_name in movements
    ]


# =============================================================================
# LOW STOCK ALERTS
# =============================================================================

@router.get("/alerts/low-stock")
async def get_low_stock_alerts(
    db: Session = Depends(get_db),
    current_user = Depends(require_permission(Permission.INVENTORY_VIEW))
):
    """
    Get materials that are below reorder level.
    
    Critical for procurement planning.
    """
    # Get materials with reorder level defined
    materials = db.query(MaterialMaster).filter(
        MaterialMaster.reorder_level > 0,
        MaterialMaster.is_active == True
    ).all()
    
    alerts = []
    
    for mat in materials:
        # Get current stock for this material
        current_stock = db.query(
            func.sum(StockLot.current_weight_kg)
        ).filter(
            StockLot.material_id == mat.id,
            StockLot.is_active == True,
            StockLot.qa_status.in_([QAStatus.APPROVED, QAStatus.CONDITIONAL])
        ).scalar() or Decimal('0')
        
        if current_stock < mat.reorder_level:
            alerts.append({
                'material_id': mat.id,
                'material_code': mat.code,
                'material_name': mat.name,
                'current_stock_kg': float(current_stock),
                'reorder_level_kg': float(mat.reorder_level),
                'shortage_kg': float(mat.reorder_level - current_stock),
                'min_order_qty': float(mat.min_order_qty) if mat.min_order_qty else 0
            })
    
    return {
        'alert_count': len(alerts),
        'alerts': sorted(alerts, key=lambda x: x['shortage_kg'], reverse=True)
    }
