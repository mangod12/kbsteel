from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, EmailStr


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str = "User"


class UserCreate(BaseModel):
    full_name: str
    email: EmailStr
    username: str
    password: str
    role: str


class UserOut(BaseModel):
    id: int
    full_name: str
    email: EmailStr
    username: str
    role: str
    created_at: datetime

    class Config:
        orm_mode = True


class CustomerCreate(BaseModel):
    name: str
    project_details: Optional[str]


class ProductionItemCreate(BaseModel):
    item_code: str
    item_name: str
    section: Optional[str]
    length_mm: Optional[int]


class ProductionItemOut(BaseModel):
    id: int
    customer_id: int
    item_code: str
    item_name: str
    section: Optional[str]
    length_mm: Optional[int]

    class Config:
        orm_mode = True


class CustomerOut(BaseModel):
    id: int
    name: str
    project_details: Optional[str]
    created_at: datetime
    production_items: Optional[List[ProductionItemOut]] = []

    class Config:
        orm_mode = True


class StageAction(BaseModel):
    production_item_id: int
    stage: str


class ProductionItemWithStages(BaseModel):
    id: int
    customer_id: int
    item_code: str
    item_name: str
    section: Optional[str]
    length_mm: Optional[int]
    stages: List["StageStatusOut"] = []

    class Config:
        orm_mode = True


class MaterialUsageCreate(BaseModel):
    production_item_id: Optional[int]
    name: str
    qty: int
    unit: Optional[str]
    by: Optional[str]


class MaterialUsageOut(BaseModel):
    id: int
    customer_id: int
    production_item_id: Optional[int]
    name: str
    qty: int
    unit: Optional[str]
    by: Optional[str]
    ts: datetime

    class Config:
        orm_mode = True


class CustomerTrackingOut(BaseModel):
    id: int
    name: str
    project: Optional[str]
    current_stage: Optional[str]
    production_items: List[ProductionItemWithStages] = []
    material_usage: List[MaterialUsageOut] = []
    stage_history: List["StageStatusOut"] = []

    class Config:
        orm_mode = True


class StageStatusOut(BaseModel):
    id: int
    production_item_id: int
    stage: str
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    updated_by: Optional[int]

    class Config:
        orm_mode = True


class QueryCreate(BaseModel):
    customer_id: int
    production_item_id: Optional[int]
    stage: Optional[str]
    description: str
    image_path: Optional[str]


class QueryOut(BaseModel):
    id: int
    customer_id: int
    production_item_id: Optional[int]
    stage: Optional[str]
    description: str
    image_path: Optional[str]
    status: str
    created_at: datetime

    class Config:
        orm_mode = True


class InstructionCreate(BaseModel):
    message: str


class InstructionOut(BaseModel):
    id: int
    message: str
    created_by: int
    created_at: datetime

    class Config:
        orm_mode = True


class InventoryIn(BaseModel):
    name: str
    unit: str | None = None
    total: int = 0
    used: int = 0
    code: str | None = None
    section: str | None = None
    category: str | None = None


class InventoryOut(BaseModel):
    id: int
    name: str
    unit: str | None = None
    total: int
    used: int
    code: str | None = None
    section: str | None = None
    category: str | None = None
    created_at: datetime | None = None

    class Config:
        orm_mode = True


class NotificationOut(BaseModel):
    id: int
    user_id: int | None = None
    role: str | None = None
    message: str
    level: str
    read: bool
    created_at: datetime

    class Config:
        orm_mode = True


class NotificationCreate(BaseModel):
    user_id: int | None = None
    role: str | None = None
    message: str
    level: str = "info"


class NotificationSettingOut(BaseModel):
    in_app: bool = True
    email: bool = False
    push: bool = False
    instr_from_boss: bool = True
    stage_changes: bool = True
    query_raised: bool = True
    query_response: bool = True
    low_inventory: bool = True
    dispatch_completed: bool = True
    updated_at: datetime | None = None

    class Config:
        orm_mode = True


class NotificationSettingIn(BaseModel):
    in_app: bool | None = None
    email: bool | None = None
    push: bool | None = None
    instr_from_boss: bool | None = None
    stage_changes: bool | None = None
    query_raised: bool | None = None
    query_response: bool | None = None
    low_inventory: bool | None = None
    dispatch_completed: bool | None = None


class RoleNotificationSettingOut(NotificationSettingOut):
    role: str


class RoleNotificationSettingIn(NotificationSettingIn):
    pass


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str
