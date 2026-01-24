"""
Steel Industry Inventory Service
================================
Proper business logic for inventory operations with:
- Transaction safety
- Audit trails
- Race condition prevention
- Weight precision handling
- FIFO/LIFO support

Author: System Architect Review
"""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List, Tuple
from enum import Enum

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from sqlalchemy.exc import IntegrityError

from ..models_v2 import (
    StockLot, StockMovement, MaterialMaster, StorageLocation,
    GoodsReceiptNote, GRNLineItem, DispatchNote, DispatchLineItem,
    MaterialConsumptionV2 as MaterialConsumption, AuditLog, NumberSequence,
    MovementType, QAStatus, DocumentStatus, WeightUnit
)


class InventoryError(Exception):
    """Base exception for inventory operations"""
    pass


class InsufficientStockError(InventoryError):
    """Raised when trying to consume more than available"""
    pass


class WeightMismatchError(InventoryError):
    """Raised when weights don't reconcile"""
    pass


class InvalidOperationError(InventoryError):
    """Raised when operation is not allowed in current state"""
    pass


# =============================================================================
# WEIGHT UTILITIES
# =============================================================================

def kg_to_tons(kg: Decimal) -> Decimal:
    """Convert kilograms to metric tons with proper precision"""
    return (kg / Decimal('1000')).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)


def tons_to_kg(tons: Decimal) -> Decimal:
    """Convert metric tons to kilograms"""
    return (tons * Decimal('1000')).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)


def normalize_weight(value: float | int | str | Decimal, unit: WeightUnit) -> Decimal:
    """
    Normalize any weight input to kilograms (internal storage unit).
    
    Args:
        value: Weight value in any format
        unit: The unit of the input value
        
    Returns:
        Weight in kilograms as Decimal
    """
    decimal_value = Decimal(str(value)).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
    
    if unit in (WeightUnit.TON, WeightUnit.MT):
        return tons_to_kg(decimal_value)
    elif unit == WeightUnit.KG:
        return decimal_value
    else:
        # For piece/meter/feet, return as-is (not weight-based)
        return decimal_value


# =============================================================================
# NUMBER SEQUENCE GENERATOR
# =============================================================================

def get_next_sequence(db: Session, sequence_name: str, prefix: str = "", year_wise: bool = True) -> str:
    """
    Thread-safe sequence number generator.
    
    Uses SELECT FOR UPDATE to prevent race conditions.
    """
    current_year = datetime.utcnow().year if year_wise else None
    
    # Lock the row for update
    seq = db.query(NumberSequence).filter(
        NumberSequence.sequence_name == sequence_name
    ).with_for_update().first()
    
    if not seq:
        # Create new sequence
        seq = NumberSequence(
            sequence_name=sequence_name,
            prefix=prefix,
            current_number=0,
            year=current_year,
            padding=6
        )
        db.add(seq)
    
    # Check if year changed (reset sequence)
    if year_wise and seq.year != current_year:
        seq.current_number = 0
        seq.year = current_year
    
    seq.current_number += 1
    db.flush()  # Ensure we get the new number
    
    # Format: PREFIX/YEAR/NUMBER
    number_str = str(seq.current_number).zfill(seq.padding)
    if year_wise:
        return f"{seq.prefix or prefix}/{current_year}/{number_str}"
    return f"{seq.prefix or prefix}/{number_str}"


# =============================================================================
# STOCK LOT OPERATIONS
# =============================================================================

class StockLotService:
    """Service class for stock lot operations"""
    
    @staticmethod
    def create_lot_from_grn(
        db: Session,
        grn_line: GRNLineItem,
        location_id: Optional[int],
        user_id: int
    ) -> StockLot:
        """
        Create a new stock lot from an approved GRN line item.
        
        This is the ONLY way to add new stock to the system.
        """
        lot_number = get_next_sequence(db, "lot", "LOT")
        
        lot = StockLot(
            lot_number=lot_number,
            material_id=grn_line.material_id,
            heat_number=grn_line.heat_number,
            batch_number=grn_line.batch_number,
            gross_weight_kg=grn_line.weight_kg,
            tare_weight_kg=Decimal('0'),
            net_weight_kg=grn_line.weight_kg,
            current_weight_kg=grn_line.weight_kg,
            initial_quantity=grn_line.received_qty,
            current_quantity=grn_line.received_qty,
            unit=grn_line.unit,
            vendor_id=grn_line.grn.vendor_id,
            grn_id=grn_line.grn_id,
            purchase_rate=grn_line.rate,
            qa_status=grn_line.qa_status,
            location_id=location_id,
            received_date=datetime.utcnow(),
            is_active=True
        )
        
        db.add(lot)
        db.flush()  # Get the ID
        
        # Create inward movement
        movement = StockMovement(
            movement_number=get_next_sequence(db, "movement", "MOV"),
            stock_lot_id=lot.id,
            movement_type=MovementType.INWARD_PURCHASE,
            weight_change_kg=lot.net_weight_kg,
            weight_before_kg=Decimal('0'),
            weight_after_kg=lot.net_weight_kg,
            quantity_change=lot.initial_quantity,
            reference_type="grn",
            reference_id=grn_line.grn_id,
            reference_number=grn_line.grn.grn_number,
            to_location_id=location_id,
            created_by=user_id,
            movement_date=datetime.utcnow()
        )
        
        db.add(movement)
        return lot
    
    @staticmethod
    def consume_from_lot(
        db: Session,
        lot_id: int,
        weight_kg: Decimal,
        user_id: int,
        reason: str,
        production_item_id: Optional[int] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[int] = None
    ) -> Tuple[StockMovement, StockLot]:
        """
        Consume material from a stock lot.
        
        Uses optimistic locking pattern to prevent race conditions.
        
        Args:
            db: Database session
            lot_id: Stock lot to consume from
            weight_kg: Weight to consume (in KG)
            user_id: User performing the action
            reason: Reason for consumption
            production_item_id: Link to production item (if applicable)
            
        Returns:
            Tuple of (movement record, updated lot)
            
        Raises:
            InsufficientStockError: If not enough stock available
            InvalidOperationError: If lot is blocked or QA pending
        """
        # Lock the lot row for update
        lot = db.query(StockLot).filter(
            StockLot.id == lot_id
        ).with_for_update().first()
        
        if not lot:
            raise HTTPException(status_code=404, detail="Stock lot not found")
        
        # Validate lot status
        if not lot.is_active:
            raise InvalidOperationError(f"Lot {lot.lot_number} is not active (fully consumed or cancelled)")
        
        if lot.is_blocked:
            raise InvalidOperationError(f"Lot {lot.lot_number} is blocked: {lot.block_reason}")
        
        if lot.qa_status not in (QAStatus.APPROVED, QAStatus.CONDITIONAL):
            raise InvalidOperationError(f"Lot {lot.lot_number} is not QA approved (status: {lot.qa_status})")
        
        # Check available stock
        weight_kg = Decimal(str(weight_kg)).quantize(Decimal('0.001'))
        
        if lot.current_weight_kg < weight_kg:
            raise InsufficientStockError(
                f"Insufficient stock in lot {lot.lot_number}. "
                f"Available: {lot.current_weight_kg} kg, Requested: {weight_kg} kg"
            )
        
        # Calculate new weight
        weight_before = lot.current_weight_kg
        weight_after = weight_before - weight_kg
        
        # Create movement record FIRST (audit trail)
        movement = StockMovement(
            movement_number=get_next_sequence(db, "movement", "MOV"),
            stock_lot_id=lot.id,
            movement_type=MovementType.CONSUMPTION,
            weight_change_kg=-weight_kg,  # Negative for outward
            weight_before_kg=weight_before,
            weight_after_kg=weight_after,
            reference_type=reference_type,
            reference_id=reference_id,
            from_location_id=lot.location_id,
            reason=reason,
            created_by=user_id,
            movement_date=datetime.utcnow()
        )
        
        db.add(movement)
        
        # Update lot
        lot.current_weight_kg = weight_after
        
        # Mark inactive if fully consumed
        if lot.current_weight_kg <= Decimal('0'):
            lot.is_active = False
            lot.current_weight_kg = Decimal('0')
        
        lot.updated_at = datetime.utcnow()
        
        # Create consumption record if linked to production
        if production_item_id:
            consumption = MaterialConsumption(
                production_item_id=production_item_id,
                stock_lot_id=lot.id,
                stock_movement_id=movement.id,
                consumed_weight_kg=weight_kg,
                consumed_by=user_id
            )
            db.add(consumption)
        
        return movement, lot
    
    @staticmethod
    def adjust_stock(
        db: Session,
        lot_id: int,
        new_weight_kg: Decimal,
        user_id: int,
        reason: str,
        approved_by: Optional[int] = None
    ) -> Tuple[StockMovement, StockLot]:
        """
        Adjust stock quantity (for reconciliation, reweighing, etc.)
        
        This creates either ADJUSTMENT_PLUS or ADJUSTMENT_MINUS movement.
        Requires approval for negative adjustments.
        """
        lot = db.query(StockLot).filter(
            StockLot.id == lot_id
        ).with_for_update().first()
        
        if not lot:
            raise HTTPException(status_code=404, detail="Stock lot not found")
        
        new_weight_kg = Decimal(str(new_weight_kg)).quantize(Decimal('0.001'))
        weight_change = new_weight_kg - lot.current_weight_kg
        
        if weight_change == 0:
            raise InvalidOperationError("No weight change specified")
        
        # Determine movement type
        if weight_change > 0:
            movement_type = MovementType.ADJUSTMENT_PLUS
        else:
            movement_type = MovementType.ADJUSTMENT_MINUS
            # Negative adjustments should be approved
            if not approved_by:
                raise InvalidOperationError("Negative stock adjustments require approval")
        
        movement = StockMovement(
            movement_number=get_next_sequence(db, "movement", "MOV"),
            stock_lot_id=lot.id,
            movement_type=movement_type,
            weight_change_kg=weight_change,
            weight_before_kg=lot.current_weight_kg,
            weight_after_kg=new_weight_kg,
            reason=reason,
            created_by=user_id,
            approved_by=approved_by,
            approved_at=datetime.utcnow() if approved_by else None,
            movement_date=datetime.utcnow()
        )
        
        db.add(movement)
        
        lot.current_weight_kg = new_weight_kg
        if lot.current_weight_kg <= 0:
            lot.is_active = False
            lot.current_weight_kg = Decimal('0')
        elif not lot.is_active and lot.current_weight_kg > 0:
            lot.is_active = True
            
        lot.updated_at = datetime.utcnow()
        
        return movement, lot
    
    @staticmethod
    def transfer_location(
        db: Session,
        lot_id: int,
        to_location_id: int,
        user_id: int,
        reason: Optional[str] = None
    ) -> Tuple[StockMovement, StockLot]:
        """Transfer a lot to a different storage location"""
        lot = db.query(StockLot).filter(
            StockLot.id == lot_id
        ).with_for_update().first()
        
        if not lot:
            raise HTTPException(status_code=404, detail="Stock lot not found")
        
        if lot.location_id == to_location_id:
            raise InvalidOperationError("Lot is already at the specified location")
        
        from_location_id = lot.location_id
        
        movement = StockMovement(
            movement_number=get_next_sequence(db, "movement", "MOV"),
            stock_lot_id=lot.id,
            movement_type=MovementType.INWARD_TRANSFER,
            weight_change_kg=Decimal('0'),  # No weight change
            weight_before_kg=lot.current_weight_kg,
            weight_after_kg=lot.current_weight_kg,
            from_location_id=from_location_id,
            to_location_id=to_location_id,
            reason=reason or "Location transfer",
            created_by=user_id,
            movement_date=datetime.utcnow()
        )
        
        db.add(movement)
        lot.location_id = to_location_id
        lot.updated_at = datetime.utcnow()
        
        return movement, lot
    
    @staticmethod
    def split_lot(
        db: Session,
        lot_id: int,
        split_weights_kg: List[Decimal],
        user_id: int,
        reason: str
    ) -> List[StockLot]:
        """
        Split a lot into multiple smaller lots.
        
        Common in steel industry when:
        - Coil is slit
        - Partial dispatch needed
        - Material is cut
        """
        lot = db.query(StockLot).filter(
            StockLot.id == lot_id
        ).with_for_update().first()
        
        if not lot:
            raise HTTPException(status_code=404, detail="Stock lot not found")
        
        total_split_weight = sum(Decimal(str(w)) for w in split_weights_kg)
        
        if total_split_weight > lot.current_weight_kg:
            raise InsufficientStockError(
                f"Total split weight ({total_split_weight} kg) exceeds available ({lot.current_weight_kg} kg)"
            )
        
        new_lots = []
        
        for weight_kg in split_weights_kg:
            weight_kg = Decimal(str(weight_kg))
            
            # Create new lot
            new_lot = StockLot(
                lot_number=get_next_sequence(db, "lot", "LOT"),
                material_id=lot.material_id,
                heat_number=lot.heat_number,
                batch_number=lot.batch_number,
                gross_weight_kg=weight_kg,
                tare_weight_kg=Decimal('0'),
                net_weight_kg=weight_kg,
                current_weight_kg=weight_kg,
                initial_quantity=Decimal('1'),
                current_quantity=Decimal('1'),
                unit=lot.unit,
                vendor_id=lot.vendor_id,
                grn_id=lot.grn_id,
                purchase_rate=lot.purchase_rate,
                qa_status=lot.qa_status,
                location_id=lot.location_id,
                received_date=lot.received_date,
                is_active=True
            )
            db.add(new_lot)
            db.flush()
            
            # Movement for new lot (split in)
            movement_in = StockMovement(
                movement_number=get_next_sequence(db, "movement", "MOV"),
                stock_lot_id=new_lot.id,
                movement_type=MovementType.SPLIT,
                weight_change_kg=weight_kg,
                weight_before_kg=Decimal('0'),
                weight_after_kg=weight_kg,
                reference_type="split_from",
                reference_id=lot.id,
                reason=f"Split from lot {lot.lot_number}",
                created_by=user_id,
                movement_date=datetime.utcnow()
            )
            db.add(movement_in)
            
            new_lots.append(new_lot)
        
        # Reduce original lot
        movement_out = StockMovement(
            movement_number=get_next_sequence(db, "movement", "MOV"),
            stock_lot_id=lot.id,
            movement_type=MovementType.SPLIT,
            weight_change_kg=-total_split_weight,
            weight_before_kg=lot.current_weight_kg,
            weight_after_kg=lot.current_weight_kg - total_split_weight,
            reason=reason,
            created_by=user_id,
            movement_date=datetime.utcnow()
        )
        db.add(movement_out)
        
        lot.current_weight_kg -= total_split_weight
        if lot.current_weight_kg <= 0:
            lot.is_active = False
            lot.current_weight_kg = Decimal('0')
        lot.updated_at = datetime.utcnow()
        
        return new_lots


# =============================================================================
# INVENTORY QUERY SERVICE
# =============================================================================

class InventoryQueryService:
    """Service for inventory queries and reports"""
    
    @staticmethod
    def get_stock_summary(
        db: Session,
        material_id: Optional[int] = None,
        location_id: Optional[int] = None,
        qa_status: Optional[QAStatus] = None,
        include_inactive: bool = False
    ) -> List[dict]:
        """
        Get aggregated stock summary by material.
        
        Returns total available weight per material.
        """
        query = db.query(
            StockLot.material_id,
            MaterialMaster.code,
            MaterialMaster.name,
            MaterialMaster.material_type,
            MaterialMaster.grade,
            func.count(StockLot.id).label('lot_count'),
            func.sum(StockLot.current_weight_kg).label('total_weight_kg'),
            func.min(StockLot.received_date).label('oldest_lot_date'),
            func.max(StockLot.received_date).label('newest_lot_date')
        ).join(
            MaterialMaster, StockLot.material_id == MaterialMaster.id
        )
        
        if not include_inactive:
            query = query.filter(StockLot.is_active == True)
        
        if material_id:
            query = query.filter(StockLot.material_id == material_id)
        
        if location_id:
            query = query.filter(StockLot.location_id == location_id)
        
        if qa_status:
            query = query.filter(StockLot.qa_status == qa_status)
        
        query = query.group_by(
            StockLot.material_id,
            MaterialMaster.code,
            MaterialMaster.name,
            MaterialMaster.material_type,
            MaterialMaster.grade
        )
        
        results = []
        for row in query.all():
            results.append({
                'material_id': row.material_id,
                'material_code': row.code,
                'material_name': row.name,
                'material_type': row.material_type,
                'grade': row.grade,
                'lot_count': row.lot_count,
                'total_weight_kg': float(row.total_weight_kg or 0),
                'total_weight_tons': float(kg_to_tons(row.total_weight_kg or Decimal('0'))),
                'oldest_lot_date': row.oldest_lot_date,
                'newest_lot_date': row.newest_lot_date
            })
        
        return results
    
    @staticmethod
    def get_stock_aging_report(
        db: Session,
        days_threshold: int = 90
    ) -> List[dict]:
        """
        Get aging report for stock management.
        
        Critical for FIFO implementation and identifying slow-moving stock.
        """
        cutoff_date = datetime.utcnow()
        
        query = db.query(
            StockLot,
            MaterialMaster.code,
            MaterialMaster.name
        ).join(
            MaterialMaster, StockLot.material_id == MaterialMaster.id
        ).filter(
            StockLot.is_active == True
        ).order_by(
            StockLot.received_date.asc()
        )
        
        results = []
        for lot, code, name in query.all():
            age_days = (cutoff_date - lot.received_date).days
            results.append({
                'lot_number': lot.lot_number,
                'material_code': code,
                'material_name': name,
                'heat_number': lot.heat_number,
                'current_weight_kg': float(lot.current_weight_kg),
                'received_date': lot.received_date,
                'age_days': age_days,
                'is_old_stock': age_days > days_threshold,
                'location_id': lot.location_id
            })
        
        return results
    
    @staticmethod
    def get_lots_for_fifo_pick(
        db: Session,
        material_id: int,
        required_weight_kg: Decimal,
        location_id: Optional[int] = None
    ) -> List[Tuple[StockLot, Decimal]]:
        """
        Get lots to pick using FIFO (First In First Out).
        
        Returns list of (lot, weight_to_pick) tuples.
        """
        query = db.query(StockLot).filter(
            StockLot.material_id == material_id,
            StockLot.is_active == True,
            StockLot.is_blocked == False,
            StockLot.qa_status.in_([QAStatus.APPROVED, QAStatus.CONDITIONAL])
        ).order_by(
            StockLot.received_date.asc()  # FIFO - oldest first
        )
        
        if location_id:
            query = query.filter(StockLot.location_id == location_id)
        
        required = Decimal(str(required_weight_kg))
        picks = []
        remaining = required
        
        for lot in query.all():
            if remaining <= 0:
                break
            
            pick_weight = min(lot.current_weight_kg, remaining)
            picks.append((lot, pick_weight))
            remaining -= pick_weight
        
        if remaining > 0:
            raise InsufficientStockError(
                f"Cannot fulfill requirement. Short by {remaining} kg"
            )
        
        return picks
    
    @staticmethod
    def reconcile_physical_vs_system(
        db: Session,
        lot_id: int,
        physical_weight_kg: Decimal
    ) -> dict:
        """
        Compare physical stock count with system record.
        
        Returns discrepancy details.
        """
        lot = db.query(StockLot).filter(StockLot.id == lot_id).first()
        
        if not lot:
            raise HTTPException(status_code=404, detail="Lot not found")
        
        physical = Decimal(str(physical_weight_kg))
        system = lot.current_weight_kg
        variance = physical - system
        variance_pct = (variance / system * 100) if system > 0 else Decimal('0')
        
        return {
            'lot_number': lot.lot_number,
            'system_weight_kg': float(system),
            'physical_weight_kg': float(physical),
            'variance_kg': float(variance),
            'variance_percent': float(variance_pct),
            'within_tolerance': abs(variance_pct) <= Decimal('0.5'),  # 0.5% tolerance
            'requires_adjustment': abs(variance_pct) > Decimal('0.5')
        }


# =============================================================================
# GRN SERVICE
# =============================================================================

class GRNService:
    """Service for Goods Receipt Note operations"""
    
    @staticmethod
    def create_grn(
        db: Session,
        vendor_id: int,
        user_id: int,
        vehicle_number: Optional[str] = None,
        vendor_invoice_number: Optional[str] = None
    ) -> GoodsReceiptNote:
        """Create a new GRN in draft status"""
        grn = GoodsReceiptNote(
            grn_number=get_next_sequence(db, "grn", "GRN"),
            vendor_id=vendor_id,
            vendor_invoice_number=vendor_invoice_number,
            vehicle_number=vehicle_number,
            status=DocumentStatus.DRAFT,
            gate_entry_time=datetime.utcnow(),
            created_by=user_id
        )
        db.add(grn)
        db.flush()
        return grn
    
    @staticmethod
    def add_line_item(
        db: Session,
        grn_id: int,
        material_id: int,
        received_qty: Decimal,
        weight_kg: Decimal,
        unit: WeightUnit = WeightUnit.KG,
        heat_number: Optional[str] = None,
        batch_number: Optional[str] = None,
        rate: Optional[Decimal] = None
    ) -> GRNLineItem:
        """Add a line item to GRN"""
        grn = db.query(GoodsReceiptNote).filter(GoodsReceiptNote.id == grn_id).first()
        
        if not grn:
            raise HTTPException(status_code=404, detail="GRN not found")
        
        if grn.status != DocumentStatus.DRAFT:
            raise InvalidOperationError("Can only add items to draft GRN")
        
        amount = (rate * received_qty) if rate else None
        
        line = GRNLineItem(
            grn_id=grn_id,
            material_id=material_id,
            heat_number=heat_number,
            batch_number=batch_number,
            received_qty=received_qty,
            weight_kg=weight_kg,
            unit=unit,
            rate=rate,
            amount=amount,
            qa_status=QAStatus.PENDING
        )
        db.add(line)
        return line
    
    @staticmethod
    def approve_grn(
        db: Session,
        grn_id: int,
        user_id: int,
        location_id: int
    ) -> Tuple[GoodsReceiptNote, List[StockLot]]:
        """
        Approve GRN and create stock lots.
        
        This is a critical transaction that:
        1. Validates all line items have QA status
        2. Creates stock lots for each approved line
        3. Records movements
        4. Updates GRN status
        """
        grn = db.query(GoodsReceiptNote).filter(
            GoodsReceiptNote.id == grn_id
        ).with_for_update().first()
        
        if not grn:
            raise HTTPException(status_code=404, detail="GRN not found")
        
        if grn.status != DocumentStatus.SUBMITTED:
            raise InvalidOperationError(f"GRN must be submitted before approval (current: {grn.status})")
        
        # Check all lines have QA decision
        pending_qa = [l for l in grn.line_items if l.qa_status == QAStatus.PENDING]
        if pending_qa:
            raise InvalidOperationError(f"{len(pending_qa)} line items pending QA inspection")
        
        created_lots = []
        
        for line in grn.line_items:
            if line.qa_status in (QAStatus.APPROVED, QAStatus.CONDITIONAL):
                lot = StockLotService.create_lot_from_grn(db, line, location_id, user_id)
                created_lots.append(lot)
        
        grn.status = DocumentStatus.APPROVED
        grn.approved_by = user_id
        grn.received_time = datetime.utcnow()
        grn.updated_at = datetime.utcnow()
        
        return grn, created_lots
