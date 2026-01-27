from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean, Float
from sqlalchemy.orm import relationship
from .db import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)  # Account status
    created_at = Column(DateTime, default=datetime.utcnow)


class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    project_details = Column(Text)
    email = Column(String, nullable=True, index=True)
    phone = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    production_items = relationship("ProductionItem", back_populates="customer")


class ProductionItem(Base):
    __tablename__ = "production_items"
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    item_code = Column(String, nullable=False)
    item_name = Column(String, nullable=False)
    section = Column(String, nullable=True)
    length_mm = Column(Integer, nullable=True)
    quantity = Column(Float, nullable=True, default=1.0)  # Quantity from Excel
    unit = Column(String, nullable=True)  # Unit from Excel
    weight_per_unit = Column(Float, nullable=True)  # Weight per unit for material calculation
    # Material requirements for this item (JSON stored as string for SQLite compatibility)
    material_requirements = Column(Text, nullable=True)  # JSON: [{"material_id": 1, "qty": 10.5}, ...]
    # Checklist for tracking progress
    checklist = Column(Text, nullable=True)  # JSON: [{"item": "Cut", "done": true}, ...]
    # Notes for the item
    notes = Column(Text, nullable=True)
    # Current stage tracking for this production item (lowercase values)
    current_stage = Column(String, nullable=False, default="fabrication")
    stage_updated_at = Column(DateTime, nullable=True)
    stage_updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Flag to track if fabrication deduction has been done (prevents double deduction)
    fabrication_deducted = Column(Boolean, default=False)
    # Also use material_deducted as an alias for FIFO deduction tracking
    material_deducted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    customer = relationship("Customer", back_populates="production_items")
    stages = relationship("StageTracking", back_populates="production_item")


class StageTracking(Base):
    __tablename__ = "stage_tracking"
    id = Column(Integer, primary_key=True, index=True)
    production_item_id = Column(Integer, ForeignKey("production_items.id"), nullable=False)
    stage = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    is_checked = Column(Boolean, default=False)
    production_item = relationship("ProductionItem", back_populates="stages")


class Query(Base):
    __tablename__ = "queries"
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    production_item_id = Column(Integer, ForeignKey("production_items.id"), nullable=True)
    stage = Column(String, nullable=True)
    description = Column(Text, nullable=False)
    image_path = Column(String, nullable=True)
    status = Column(String, nullable=False, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)


class Instruction(Base):
    __tablename__ = "instructions"
    id = Column(Integer, primary_key=True, index=True)
    message = Column(Text, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True)


class MaterialUsage(Base):
    __tablename__ = "material_usage"
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    production_item_id = Column(Integer, ForeignKey("production_items.id"), nullable=True)
    name = Column(String, nullable=False)
    qty = Column(Float, nullable=False)  # Changed to Float for decimal quantities
    unit = Column(String, nullable=True)
    by = Column(String, nullable=True)
    applied = Column(Boolean, default=False)  # Whether this usage has been applied to inventory
    ts = Column(DateTime, default=datetime.utcnow)


class Inventory(Base):
    __tablename__ = "inventory"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    unit = Column(String, nullable=True)
    total = Column(Float, nullable=False, default=0.0)  # Changed to Float for decimal quantities
    used = Column(Float, nullable=False, default=0.0)   # Changed to Float for decimal quantities
    # Optional metadata fields to support richer searching/filtering
    code = Column(String, nullable=True)
    section = Column(String, nullable=True)
    category = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    role = Column(String, nullable=True)  # target role (optional)
    message = Column(Text, nullable=False)
    level = Column(String, nullable=False, default="info")
    read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class NotificationSetting(Base):
    __tablename__ = "notification_settings"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    in_app = Column(Boolean, default=True)
    email = Column(Boolean, default=False)
    push = Column(Boolean, default=False)
    # Per-event toggles
    instr_from_boss = Column(Boolean, default=True)
    stage_changes = Column(Boolean, default=True)
    query_raised = Column(Boolean, default=True)
    query_response = Column(Boolean, default=True)
    low_inventory = Column(Boolean, default=True)
    dispatch_completed = Column(Boolean, default=True)
    updated_at = Column(DateTime, default=datetime.utcnow)


class MaterialConsumption(Base):
    __tablename__ = "material_consumption"
    id = Column(Integer, primary_key=True, index=True)
    material_usage_id = Column(Integer, ForeignKey("material_usage.id"), nullable=False)
    inventory_id = Column(Integer, ForeignKey("inventory.id"), nullable=False)
    qty = Column(Float, nullable=False)
    ts = Column(DateTime, default=datetime.utcnow)


class TrackingStageHistory(Base):
    __tablename__ = "tracking_stage_history"
    id = Column(Integer, primary_key=True, index=True)
    material_id = Column(Integer, ForeignKey("production_items.id"), nullable=False)
    from_stage = Column(String, nullable=True)
    to_stage = Column(String, nullable=True)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    changed_at = Column(DateTime, default=datetime.utcnow)
    remarks = Column(Text, nullable=True)


class RoleNotificationSetting(Base):
    __tablename__ = "role_notification_settings"
    id = Column(Integer, primary_key=True, index=True)
    role = Column(String, unique=True, nullable=False)
    in_app = Column(Boolean, default=True)
    email = Column(Boolean, default=False)
    push = Column(Boolean, default=False)
    # Per-event toggles for role defaults
    instr_from_boss = Column(Boolean, default=True)
    stage_changes = Column(Boolean, default=True)
    query_raised = Column(Boolean, default=True)
    query_response = Column(Boolean, default=True)
    low_inventory = Column(Boolean, default=True)
    dispatch_completed = Column(Boolean, default=True)
    updated_at = Column(DateTime, default=datetime.utcnow)


class ScrapRecord(Base):
    """Track scrap/waste materials after dispatch"""
    __tablename__ = "scrap_records"
    id = Column(Integer, primary_key=True, index=True)
    material_name = Column(String, nullable=False)
    weight_kg = Column(Float, nullable=False)
    length_mm = Column(Float, nullable=True)  # Dimension for matching
    width_mm = Column(Float, nullable=True)
    quantity = Column(Integer, default=1)  # Number of pieces
    reason_code = Column(String, nullable=False)  # cutting_waste, defect, damage, overrun, leftover
    source_item_id = Column(Integer, ForeignKey("production_items.id"), nullable=True)
    source_customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    dimensions = Column(String, nullable=True)  # Text description e.g., "200mm x 50mm x 6m"
    notes = Column(Text, nullable=True)
    status = Column(String, default="pending")  # pending, returned_to_inventory, disposed, recycled, sold
    scrap_value = Column(Float, nullable=True)  # Sale value if sold
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ReusableStock(Base):
    """Track reusable offcuts and leftover pieces that can be used again"""
    __tablename__ = "reusable_stock"
    id = Column(Integer, primary_key=True, index=True)
    material_name = Column(String, nullable=False)
    length_mm = Column(Float, nullable=True)  # For dimension matching
    width_mm = Column(Float, nullable=True)
    weight_kg = Column(Float, nullable=False)
    quantity = Column(Integer, default=1)  # Number of pieces
    dimensions = Column(String, nullable=False)  # Text e.g., "1200mm x 150mm beam"
    source_item_id = Column(Integer, ForeignKey("production_items.id"), nullable=True)
    source_customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    quality_grade = Column(String, default="A")  # A=good, B=minor defects, C=usable with caution
    notes = Column(Text, nullable=True)
    is_available = Column(Boolean, default=True)
    used_in_item_id = Column(Integer, ForeignKey("production_items.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

