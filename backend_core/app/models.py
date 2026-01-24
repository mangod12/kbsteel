from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean
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
    created_at = Column(DateTime, default=datetime.utcnow)


class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    project_details = Column(Text)
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


class MaterialUsage(Base):
    __tablename__ = "material_usage"
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    production_item_id = Column(Integer, ForeignKey("production_items.id"), nullable=True)
    name = Column(String, nullable=False)
    qty = Column(Integer, nullable=False)
    unit = Column(String, nullable=True)
    by = Column(String, nullable=True)
    ts = Column(DateTime, default=datetime.utcnow)


class Inventory(Base):
    __tablename__ = "inventory"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    unit = Column(String, nullable=True)
    total = Column(Integer, nullable=False, default=0)
    used = Column(Integer, nullable=False, default=0)
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

