"""
Steel Industry Inventory Management - Improved Data Models
===========================================================
This module provides a comprehensive data model suitable for real steel plant operations.

Key Features:
- Proper weight handling with Decimal precision
- Full lot/heat traceability
- Movement audit trail
- GRN and dispatch documentation
- QA hold/release workflow
- Location-based inventory (yard/warehouse/rack)

Author: System Architect Review
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, List

from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, Text, Boolean,
    Numeric, Enum as SQLEnum, Index, CheckConstraint, UniqueConstraint,
    event
)
from sqlalchemy.orm import relationship, validates
from .db import Base


# =============================================================================
# ENUMS - Type Safety for Steel Operations
# =============================================================================

class MaterialType(str, Enum):
    """Standard steel material types"""
    COIL = "coil"
    BILLET = "billet"
    SLAB = "slab"
    SHEET = "sheet"
    BAR = "bar"
    PLATE = "plate"
    PIPE = "pipe"
    ANGLE = "angle"
    CHANNEL = "channel"
    BEAM = "beam"
    SCRAP = "scrap"
    OTHER = "other"


class WeightUnit(str, Enum):
    """Weight units with conversion factors"""
    KG = "kg"
    TON = "ton"  # Metric ton (1000 kg)
    MT = "mt"    # Metric ton (alias)
    PIECE = "pcs"
    METER = "m"
    FEET = "ft"


class MovementType(str, Enum):
    """All possible inventory movements"""
    INWARD_PURCHASE = "inward_purchase"
    INWARD_RETURN = "inward_return"
    INWARD_TRANSFER = "inward_transfer"
    OUTWARD_SALE = "outward_sale"
    OUTWARD_TRANSFER = "outward_transfer"
    OUTWARD_SCRAP = "outward_scrap"
    CONSUMPTION = "consumption"
    ADJUSTMENT_PLUS = "adjustment_plus"
    ADJUSTMENT_MINUS = "adjustment_minus"
    REWEIGH = "reweigh"
    SPLIT = "split"
    MERGE = "merge"


class QAStatus(str, Enum):
    """Quality assurance statuses"""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ON_HOLD = "on_hold"
    CONDITIONAL = "conditional"


class DocumentStatus(str, Enum):
    """Document workflow status"""
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    CANCELLED = "cancelled"


# =============================================================================
# USER & ACCESS CONTROL
# =============================================================================

# User model is imported from models.py to avoid conflict
# Use the User model from .models module instead of redefining


# =============================================================================
# MASTER DATA
# =============================================================================

class MaterialMaster(Base):
    """
    Master catalog of materials - defines WHAT can be stocked.
    Separate from actual inventory quantities.
    """
    __tablename__ = "material_master"
    
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False, index=True)  # e.g., "STL-COIL-HR-2.5"
    name = Column(String(200), nullable=False)
    material_type = Column(SQLEnum(MaterialType), nullable=False)
    grade = Column(String(50), nullable=True)  # IS 2062 E250, ASTM A36, etc.
    specification = Column(Text, nullable=True)  # Technical specs
    
    # Dimensions (nullable - depends on material type)
    thickness_mm = Column(Numeric(10, 3), nullable=True)
    width_mm = Column(Numeric(10, 2), nullable=True)
    length_mm = Column(Numeric(10, 2), nullable=True)
    diameter_mm = Column(Numeric(10, 2), nullable=True)
    
    # Default units and thresholds
    default_unit = Column(SQLEnum(WeightUnit), default=WeightUnit.KG)
    reorder_level = Column(Numeric(15, 3), default=0)  # Alert when stock below this
    min_order_qty = Column(Numeric(15, 3), default=0)
    
    # Categorization
    category = Column(String(50), nullable=True)
    sub_category = Column(String(50), nullable=True)
    hsn_code = Column(String(20), nullable=True)  # For GST compliance in India
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    stock_lots = relationship("StockLot", back_populates="material")
    
    __table_args__ = (
        Index('ix_material_type_grade', 'material_type', 'grade'),
    )


class Vendor(Base):
    """Vendor/Supplier master data"""
    __tablename__ = "vendors"
    
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    gstin = Column(String(20), nullable=True)  # GST number
    pan = Column(String(20), nullable=True)
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    pincode = Column(String(10), nullable=True)
    contact_person = Column(String(100), nullable=True)
    phone = Column(String(20), nullable=True)
    email = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    grns = relationship("GoodsReceiptNote", back_populates="vendor")
    stock_lots = relationship("StockLot", back_populates="vendor")


class StorageLocation(Base):
    """
    Physical storage locations - Yard, Warehouse, Rack positions.
    Hierarchical: Site → Warehouse → Zone → Rack → Position
    """
    __tablename__ = "storage_locations"
    
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False)  # e.g., "WH1-A-01-03"
    name = Column(String(100), nullable=False)
    location_type = Column(String(20), nullable=False)  # yard, warehouse, rack
    
    # Hierarchy
    parent_id = Column(Integer, ForeignKey("storage_locations.id"), nullable=True)
    
    # Capacity (optional)
    capacity_tons = Column(Numeric(15, 3), nullable=True)
    current_occupancy_tons = Column(Numeric(15, 3), default=0)
    
    # Properties
    is_covered = Column(Boolean, default=True)  # Important for rust-prone materials
    is_active = Column(Boolean, default=True)
    
    # Relationships
    children = relationship("StorageLocation", backref="parent", remote_side=[id])
    stock_lots = relationship("StockLot", back_populates="location")


# =============================================================================
# INVENTORY - THE CORE
# =============================================================================

class StockLot(Base):
    """
    Individual lot/batch of material in stock.
    
    This is the heart of steel inventory - each row represents a specific
    batch with its own heat number, quality status, and location.
    
    Key Design Decisions:
    1. Weight stored in KG (converted on display)
    2. Decimal precision for accurate weighment
    3. Immutable base quantities - changes via movements only
    4. Full traceability via heat_number and lot_number
    """
    __tablename__ = "stock_lots"
    
    id = Column(Integer, primary_key=True, index=True)
    lot_number = Column(String(50), unique=True, nullable=False, index=True)  # System-generated
    
    # Material reference
    material_id = Column(Integer, ForeignKey("material_master.id"), nullable=False)
    
    # Traceability - CRITICAL for steel industry
    heat_number = Column(String(50), nullable=True, index=True)  # Mill heat number
    batch_number = Column(String(50), nullable=True)  # Vendor batch
    coil_number = Column(String(50), nullable=True)  # For coils
    
    # Weight Management (ALL in KG for consistency)
    gross_weight_kg = Column(Numeric(15, 3), nullable=False)  # Total weight including packing
    tare_weight_kg = Column(Numeric(15, 3), default=0)  # Packing/container weight
    net_weight_kg = Column(Numeric(15, 3), nullable=False)  # Actual material weight
    current_weight_kg = Column(Numeric(15, 3), nullable=False)  # Available after consumption
    
    # Quantity (for piece-counted items)
    initial_quantity = Column(Numeric(15, 3), default=1)
    current_quantity = Column(Numeric(15, 3), default=1)
    unit = Column(SQLEnum(WeightUnit), default=WeightUnit.KG)
    
    # Procurement info
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True)
    grn_id = Column(Integer, ForeignKey("goods_receipt_notes.id"), nullable=True)
    purchase_rate = Column(Numeric(15, 2), nullable=True)  # Rate per unit
    
    # Quality
    qa_status = Column(SQLEnum(QAStatus), default=QAStatus.PENDING)
    qa_remarks = Column(Text, nullable=True)
    test_certificate_ref = Column(String(100), nullable=True)
    
    # Location
    location_id = Column(Integer, ForeignKey("storage_locations.id"), nullable=True)
    
    # Dates
    received_date = Column(DateTime, nullable=False)
    manufacture_date = Column(DateTime, nullable=True)
    expiry_date = Column(DateTime, nullable=True)  # For coated materials
    
    # Status
    is_active = Column(Boolean, default=True)  # False when fully consumed
    is_blocked = Column(Boolean, default=False)  # Manual block for disputes
    block_reason = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    material = relationship("MaterialMaster", back_populates="stock_lots")
    vendor = relationship("Vendor", back_populates="stock_lots")
    location = relationship("StorageLocation", back_populates="stock_lots")
    grn = relationship("GoodsReceiptNote", back_populates="stock_lots")
    movements = relationship("StockMovement", back_populates="stock_lot")
    
    __table_args__ = (
        # Ensure current weight never exceeds net weight
        CheckConstraint('current_weight_kg >= 0', name='ck_current_weight_positive'),
        CheckConstraint('current_weight_kg <= net_weight_kg', name='ck_current_not_exceed_net'),
        CheckConstraint('net_weight_kg <= gross_weight_kg', name='ck_net_not_exceed_gross'),
        Index('ix_stock_lot_heat', 'heat_number'),
        Index('ix_stock_lot_material_qa', 'material_id', 'qa_status'),
        Index('ix_stock_lot_location', 'location_id'),
        Index('ix_stock_lot_vendor_date', 'vendor_id', 'received_date'),
    )
    
    @validates('current_weight_kg')
    def validate_current_weight(self, key, value):
        """Prevent negative stock"""
        if value < 0:
            raise ValueError("Current weight cannot be negative")
        return value
    
    @property
    def age_days(self) -> int:
        """Calculate stock age for FIFO/aging reports"""
        return (datetime.utcnow() - self.received_date).days
    
    @property
    def is_low_stock(self) -> bool:
        """Check if below 15% of original"""
        if self.net_weight_kg > 0:
            return float(self.current_weight_kg / self.net_weight_kg) < 0.15
        return False


class StockMovement(Base):
    """
    Immutable audit log of every stock change.
    
    CRITICAL: Stock changes should NEVER be made directly to StockLot.
    All changes flow through StockMovement which updates the lot atomically.
    
    This provides:
    1. Complete audit trail
    2. Reconciliation capability
    3. Movement reversal support
    4. Report generation
    """
    __tablename__ = "stock_movements"
    
    id = Column(Integer, primary_key=True, index=True)
    movement_number = Column(String(50), unique=True, nullable=False)  # System-generated
    
    # What moved
    stock_lot_id = Column(Integer, ForeignKey("stock_lots.id"), nullable=False)
    movement_type = Column(SQLEnum(MovementType), nullable=False)
    
    # Weight change (positive for inward, negative for outward)
    weight_change_kg = Column(Numeric(15, 3), nullable=False)
    weight_before_kg = Column(Numeric(15, 3), nullable=False)  # Snapshot
    weight_after_kg = Column(Numeric(15, 3), nullable=False)   # Snapshot
    
    # Quantity change (for piece-counted)
    quantity_change = Column(Numeric(15, 3), default=0)
    
    # Reference documents
    reference_type = Column(String(50), nullable=True)  # grn, dispatch, adjustment, etc.
    reference_id = Column(Integer, nullable=True)
    reference_number = Column(String(50), nullable=True)  # External reference
    
    # Location tracking
    from_location_id = Column(Integer, ForeignKey("storage_locations.id"), nullable=True)
    to_location_id = Column(Integer, ForeignKey("storage_locations.id"), nullable=True)
    
    # Reason and remarks
    reason = Column(String(200), nullable=True)
    remarks = Column(Text, nullable=True)
    
    # Approval workflow
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    
    # Timestamps (immutable after creation)
    movement_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Reversal tracking
    is_reversed = Column(Boolean, default=False)
    reversed_by_movement_id = Column(Integer, ForeignKey("stock_movements.id"), nullable=True)
    reversal_of_movement_id = Column(Integer, ForeignKey("stock_movements.id"), nullable=True)
    
    # Relationships
    stock_lot = relationship("StockLot", back_populates="movements")
    # User relationships - foreign keys reference users table from models.py
    from_location = relationship("StorageLocation", foreign_keys=[from_location_id])
    to_location = relationship("StorageLocation", foreign_keys=[to_location_id])
    
    __table_args__ = (
        Index('ix_movement_lot_date', 'stock_lot_id', 'movement_date'),
        Index('ix_movement_type_date', 'movement_type', 'movement_date'),
        Index('ix_movement_reference', 'reference_type', 'reference_id'),
    )


# =============================================================================
# INWARD DOCUMENTS
# =============================================================================

class GoodsReceiptNote(Base):
    """
    GRN - Official inward document for all material receipts.
    
    Workflow:
    1. Gate entry creates draft GRN
    2. Weighbridge captures weights
    3. Store receives and verifies
    4. QA inspects (optional)
    5. GRN approved → Stock lots created
    """
    __tablename__ = "goods_receipt_notes"
    
    id = Column(Integer, primary_key=True, index=True)
    grn_number = Column(String(50), unique=True, nullable=False, index=True)
    
    # Vendor info
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    vendor_invoice_number = Column(String(50), nullable=True)
    vendor_invoice_date = Column(DateTime, nullable=True)
    
    # Vehicle info (gate entry)
    vehicle_number = Column(String(20), nullable=True)
    driver_name = Column(String(100), nullable=True)
    driver_contact = Column(String(20), nullable=True)
    
    # Weighbridge data
    gross_weight_kg = Column(Numeric(15, 3), nullable=True)
    tare_weight_kg = Column(Numeric(15, 3), nullable=True)
    net_weight_kg = Column(Numeric(15, 3), nullable=True)
    weighbridge_slip_number = Column(String(50), nullable=True)
    
    # Status
    status = Column(SQLEnum(DocumentStatus), default=DocumentStatus.DRAFT)
    
    # Timestamps
    gate_entry_time = Column(DateTime, nullable=True)
    weighment_time = Column(DateTime, nullable=True)
    received_time = Column(DateTime, nullable=True)
    
    # Users
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    received_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    remarks = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    vendor = relationship("Vendor", back_populates="grns")
    line_items = relationship("GRNLineItem", back_populates="grn")
    stock_lots = relationship("StockLot", back_populates="grn")


class GRNLineItem(Base):
    """Individual items in a GRN"""
    __tablename__ = "grn_line_items"
    
    id = Column(Integer, primary_key=True, index=True)
    grn_id = Column(Integer, ForeignKey("goods_receipt_notes.id"), nullable=False)
    
    material_id = Column(Integer, ForeignKey("material_master.id"), nullable=False)
    heat_number = Column(String(50), nullable=True)
    batch_number = Column(String(50), nullable=True)
    
    # Quantities
    ordered_qty = Column(Numeric(15, 3), nullable=True)
    received_qty = Column(Numeric(15, 3), nullable=False)
    accepted_qty = Column(Numeric(15, 3), nullable=True)  # After QA
    rejected_qty = Column(Numeric(15, 3), default=0)
    
    unit = Column(SQLEnum(WeightUnit), default=WeightUnit.KG)
    
    # Weight
    weight_kg = Column(Numeric(15, 3), nullable=False)
    
    # Pricing
    rate = Column(Numeric(15, 2), nullable=True)
    amount = Column(Numeric(15, 2), nullable=True)
    
    # QA
    qa_status = Column(SQLEnum(QAStatus), default=QAStatus.PENDING)
    qa_remarks = Column(Text, nullable=True)
    
    # Relationships
    grn = relationship("GoodsReceiptNote", back_populates="line_items")
    material = relationship("MaterialMaster")


# =============================================================================
# OUTWARD DOCUMENTS
# =============================================================================

class DispatchNote(Base):
    """
    Outward dispatch documentation.
    
    Workflow:
    1. Sales creates dispatch request
    2. Store picks material (creates reservations)
    3. Weighbridge captures final weights
    4. Dispatch approved → Stock reduced
    """
    __tablename__ = "dispatch_notes"
    
    id = Column(Integer, primary_key=True, index=True)
    dispatch_number = Column(String(50), unique=True, nullable=False, index=True)
    
    # Customer info
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    sales_order_ref = Column(String(50), nullable=True)
    
    # Vehicle
    vehicle_number = Column(String(20), nullable=True)
    transporter = Column(String(100), nullable=True)
    driver_name = Column(String(100), nullable=True)
    driver_contact = Column(String(20), nullable=True)
    
    # Weighbridge
    gross_weight_kg = Column(Numeric(15, 3), nullable=True)
    tare_weight_kg = Column(Numeric(15, 3), nullable=True)
    net_weight_kg = Column(Numeric(15, 3), nullable=True)
    
    # Status
    status = Column(SQLEnum(DocumentStatus), default=DocumentStatus.DRAFT)
    
    # Timestamps
    requested_at = Column(DateTime, default=datetime.utcnow)
    dispatched_at = Column(DateTime, nullable=True)
    
    # Users
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    remarks = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships - Customer is from models.py
    line_items = relationship("DispatchLineItem", back_populates="dispatch")


class DispatchLineItem(Base):
    """Individual items in a dispatch"""
    __tablename__ = "dispatch_line_items"
    
    id = Column(Integer, primary_key=True, index=True)
    dispatch_id = Column(Integer, ForeignKey("dispatch_notes.id"), nullable=False)
    stock_lot_id = Column(Integer, ForeignKey("stock_lots.id"), nullable=False)
    
    # Quantities
    dispatched_weight_kg = Column(Numeric(15, 3), nullable=False)
    dispatched_qty = Column(Numeric(15, 3), default=1)
    
    # Pricing
    rate = Column(Numeric(15, 2), nullable=True)
    amount = Column(Numeric(15, 2), nullable=True)
    
    # Relationships
    dispatch = relationship("DispatchNote", back_populates="line_items")
    stock_lot = relationship("StockLot")


# =============================================================================
# CUSTOMER & PRODUCTION (Enhanced from original)
# =============================================================================

# Customer model exists in models.py - use that instead
# from .models import Customer  # Reuse existing Customer

class ProductionItemV2(Base):
    """Enhanced production item with proper weight tracking"""
    __tablename__ = "production_items_v2"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    
    item_code = Column(String(50), nullable=False)
    item_name = Column(String(200), nullable=False)
    section = Column(String(100), nullable=True)
    
    # Dimensions with proper precision
    length_mm = Column(Numeric(10, 2), nullable=True)
    width_mm = Column(Numeric(10, 2), nullable=True)
    thickness_mm = Column(Numeric(10, 3), nullable=True)
    
    # Quantity and weight
    ordered_qty = Column(Numeric(15, 3), default=1)
    produced_qty = Column(Numeric(15, 3), default=0)
    
    # Weight tracking
    estimated_weight_kg = Column(Numeric(15, 3), nullable=True)
    actual_weight_kg = Column(Numeric(15, 3), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships - reference existing Customer model from models.py
    # customer relationship will be established at runtime
    stages = relationship("StageTrackingV2", back_populates="production_item")
    material_consumption = relationship("MaterialConsumptionV2", back_populates="production_item")


class StageTrackingV2(Base):
    """Production stage tracking - unchanged but with better indexing"""
    __tablename__ = "stage_tracking_v2"
    
    id = Column(Integer, primary_key=True, index=True)
    production_item_id = Column(Integer, ForeignKey("production_items_v2.id"), nullable=False)
    stage = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    remarks = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    production_item = relationship("ProductionItemV2", back_populates="stages")
    
    __table_args__ = (
        Index('ix_stage_v2_item_status', 'production_item_id', 'status'),
        UniqueConstraint('production_item_id', 'stage', name='uq_item_stage_v2'),
    )


class MaterialConsumptionV2(Base):
    """
    Track material consumption against production items.
    Links stock lots to production for traceability.
    """
    __tablename__ = "material_consumption_v2"
    
    id = Column(Integer, primary_key=True, index=True)
    production_item_id = Column(Integer, ForeignKey("production_items_v2.id"), nullable=False)
    stock_lot_id = Column(Integer, ForeignKey("stock_lots.id"), nullable=False)
    stock_movement_id = Column(Integer, ForeignKey("stock_movements.id"), nullable=True)
    
    consumed_weight_kg = Column(Numeric(15, 3), nullable=False)
    consumed_qty = Column(Numeric(15, 3), default=1)
    
    stage = Column(String(50), nullable=True)  # At which production stage
    consumed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    consumed_at = Column(DateTime, default=datetime.utcnow)
    
    remarks = Column(Text, nullable=True)
    
    # Relationships
    production_item = relationship("ProductionItemV2", back_populates="material_consumption")
    stock_lot = relationship("StockLot")


# =============================================================================
# SYSTEM TABLES
# =============================================================================

class AuditLog(Base):
    """
    General audit log for all system changes.
    Separate from stock movements - captures ALL entity changes.
    """
    __tablename__ = "audit_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # What changed
    entity_type = Column(String(50), nullable=False)  # Table name
    entity_id = Column(Integer, nullable=False)
    action = Column(String(20), nullable=False)  # create, update, delete
    
    # Change details (JSON stored as text for SQLite compatibility)
    old_values = Column(Text, nullable=True)
    new_values = Column(Text, nullable=True)
    
    # Who and when
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(255), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index('ix_audit_entity', 'entity_type', 'entity_id'),
        Index('ix_audit_user_date', 'user_id', 'created_at'),
    )


class SystemConfig(Base):
    """System configuration key-value store"""
    __tablename__ = "system_config"
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    description = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class NumberSequence(Base):
    """
    Auto-increment sequences for document numbers.
    Allows prefix customization and year-wise reset.
    """
    __tablename__ = "number_sequences"
    
    id = Column(Integer, primary_key=True, index=True)
    sequence_name = Column(String(50), unique=True, nullable=False)  # grn, dispatch, lot, etc.
    prefix = Column(String(20), default="")
    current_number = Column(Integer, default=0)
    year = Column(Integer, nullable=True)  # For yearly reset
    padding = Column(Integer, default=6)  # Number of digits
    
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
