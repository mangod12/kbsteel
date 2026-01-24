from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from sqlalchemy import select, exists, and_, or_, func

from . import models, schemas
from .deps import get_db, require_role

router = APIRouter(prefix="/tracking", tags=["tracking"])

STAGE_ORDER = ["fabrication", "painting", "dispatch"]


def _capitalize_stage(s: str) -> str:
    return s.capitalize() if s else s


@router.post("/start-stage", response_model=schemas.StageStatusOut)
def start_stage(action: schemas.StageAction, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor"))):
    item = db.query(models.ProductionItem).filter(models.ProductionItem.id == action.production_item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Production item not found")
    stage = action.stage
    if stage not in STAGE_ORDER:
        raise HTTPException(status_code=400, detail="Invalid stage")

    idx = STAGE_ORDER.index(stage)
    if idx > 0:
        prev = STAGE_ORDER[idx - 1]
        prev_row = db.query(models.StageTracking).filter(models.StageTracking.production_item_id == item.id, models.StageTracking.stage == prev, models.StageTracking.status == "completed").first()
        if not prev_row:
            raise HTTPException(status_code=400, detail=f"Previous stage '{prev}' must be completed before starting '{stage}'")

    inprog = db.query(models.StageTracking).filter(models.StageTracking.production_item_id == item.id, models.StageTracking.status == "in_progress").first()
    if inprog:
        raise HTTPException(status_code=400, detail="Another stage is already in progress for this item")

    row = db.query(models.StageTracking).filter(models.StageTracking.production_item_id == item.id, models.StageTracking.stage == stage).first()
    now = datetime.utcnow()
    if row:
        row.status = "in_progress"
        row.started_at = now
        row.updated_by = current_user.id
    else:
        row = models.StageTracking(production_item_id=item.id, stage=stage, status="in_progress", started_at=now, updated_by=current_user.id)
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/complete-stage", response_model=schemas.StageStatusOut)
def complete_stage(action: schemas.StageAction, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor"))):
    item = db.query(models.ProductionItem).filter(models.ProductionItem.id == action.production_item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Production item not found")
    stage = action.stage
    if stage not in STAGE_ORDER:
        raise HTTPException(status_code=400, detail="Invalid stage")

    row = db.query(models.StageTracking).filter(models.StageTracking.production_item_id == item.id, models.StageTracking.stage == stage).first()
    if not row or row.status != "in_progress":
        raise HTTPException(status_code=400, detail="Stage is not in progress")
    row.status = "completed"
    row.completed_at = datetime.utcnow()
    row.updated_by = current_user.id
    db.commit()
    db.refresh(row)
    return row


def _serialize_stage(row: models.StageTracking) -> schemas.StageStatusOut:
    return schemas.StageStatusOut.from_orm(row)


def _serialize_item_with_stages(item: models.ProductionItem, db: Session) -> schemas.ProductionItemWithStages:
    stages = db.query(models.StageTracking).filter(models.StageTracking.production_item_id == item.id).all()
    return schemas.ProductionItemWithStages(
        id=item.id,
        customer_id=item.customer_id,
        item_code=item.item_code,
        item_name=item.item_name,
        section=item.section,
        length_mm=item.length_mm,
        stages=[_serialize_stage(s) for s in stages],
    )


@router.get("/customer/{customer_id}", response_model=schemas.CustomerTrackingOut)
def get_customer_tracking(customer_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor", "User"))):
    cust = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found")

    items_with = []
    all_stage_rows = []
    for item in cust.production_items:
        p = _serialize_item_with_stages(item, db)
        items_with.append(p)
        # gather stages for history
        rows = db.query(models.StageTracking).filter(models.StageTracking.production_item_id == item.id).all()
        all_stage_rows.extend(rows)

    # material usage
    mu_rows = db.query(models.MaterialUsage).filter(models.MaterialUsage.customer_id == cust.id).order_by(models.MaterialUsage.ts.desc()).all()
    mu_serialized = [schemas.MaterialUsageOut.from_orm(m) for m in mu_rows]

    # stage history - flatten and sort by timestamp
    history = []
    for r in all_stage_rows:
        # prefer started_at then completed_at
        ts = r.started_at or r.completed_at
        history.append(r)
    # sort by started_at/completed_at descending
    history_sorted = sorted(history, key=lambda x: x.started_at or x.completed_at or datetime.min, reverse=True)
    history_serialized = [_serialize_stage(r) for r in history_sorted]

    # compute customer-level current stage (simple heuristic)
    cur_stage = None
    # if any in_progress, take that
    inprog = [r for r in all_stage_rows if r.status == 'in_progress']
    if inprog:
        cur_stage = _capitalize_stage(inprog[0].stage)
    else:
        # find max completed stage across items
        completed = [r for r in all_stage_rows if r.status == 'completed']
        if not completed:
            cur_stage = 'Pending'
        else:
            # choose highest-order completed
            completed_stages = {r.stage for r in completed}
            for s in reversed(STAGE_ORDER):
                if s in completed_stages:
                    cur_stage = _capitalize_stage(s)
                    break

    return schemas.CustomerTrackingOut(
        id=cust.id,
        name=cust.name,
        project=cust.project_details,
        current_stage=cur_stage,
        production_items=items_with,
        material_usage=mu_serialized,
        stage_history=history_serialized,
    )


# --- Compatibility adapter endpoints for legacy frontend ---


@router.get("/customers", response_model=List[dict])
def list_customers_compat(
    # customer-level filters
    name: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    # production item / stage filters
    item_name: Optional[str] = Query(None),
    item_code: Optional[str] = Query(None),
    section: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    stage_status: Optional[str] = Query(None),
    length_min: Optional[int] = Query(None),
    length_max: Optional[int] = Query(None),
    quantity_min: Optional[int] = Query(None),
    quantity_max: Optional[int] = Query(None),
    date_stage_from: Optional[str] = Query(None),
    date_stage_to: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("Boss", "Software Supervisor", "User")),
):
    """
    Compatibility list endpoint extended with production-item & stage filters.
    When no item/stage filters are provided it behaves exactly as before.
    All provided filters are combined with AND logic.
    """
    cust_query = db.query(models.Customer)
    if name:
        cust_query = cust_query.filter(models.Customer.name.ilike(f"%{name}%"))
    if project:
        cust_query = cust_query.filter(models.Customer.project_details.ilike(f"%{project}%"))

    # date range on customer.created_at if provided
    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from)
            cust_query = cust_query.filter(models.Customer.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to)
            cust_query = cust_query.filter(models.Customer.created_at <= dt_to)
        except ValueError:
            pass

    # Determine if production-item/stage filters were supplied
    has_item_filters = any([item_name, item_code, section, stage, stage_status, length_min is not None, length_max is not None, quantity_min is not None, quantity_max is not None, date_stage_from, date_stage_to])

    customers = cust_query.all()

    def compute_current_stage_for_customer(cust: models.Customer):
        all_stage_rows = []
        for item in cust.production_items:
            rows = db.query(models.StageTracking).filter(models.StageTracking.production_item_id == item.id).all()
            all_stage_rows.extend(rows)
        inprog = [r for r in all_stage_rows if r.status == 'in_progress']
        if inprog:
            return _capitalize_stage(inprog[0].stage)
        completed = [r for r in all_stage_rows if r.status == 'completed']
        if not completed:
            return 'Pending'
        completed_stages = {r.stage for r in completed}
        for s in reversed(STAGE_ORDER):
            if s in completed_stages:
                return _capitalize_stage(s)
        return 'Pending'

    if not has_item_filters:
        out = []
        for c in customers:
            out.append({"id": c.id, "name": c.name, "current_stage": compute_current_stage_for_customer(c)})
        return out

    # Build filters for ProductionItem + StageTracking
    pi = models.ProductionItem
    st = models.StageTracking
    filters = []
    if item_name:
        filters.append(pi.item_name.ilike(f"%{item_name}%"))
    if item_code:
        filters.append(pi.item_code.ilike(f"%{item_code}%"))
    if section:
        filters.append(pi.section.ilike(f"%{section}%"))
    if length_min is not None:
        filters.append(pi.length_mm >= length_min)
    if length_max is not None:
        filters.append(pi.length_mm <= length_max)
    if stage:
        try:
            filters.append(func.lower(st.stage) == stage.lower())
        except Exception:
            filters.append(st.stage == stage)
    if stage_status:
        filters.append(st.status == stage_status)
    if date_stage_from:
        try:
            dsf = datetime.fromisoformat(date_stage_from)
            filters.append(or_(st.started_at >= dsf, st.completed_at >= dsf))
        except ValueError:
            pass
    if date_stage_to:
        try:
            dst = datetime.fromisoformat(date_stage_to)
            filters.append(or_(st.started_at <= dst, st.completed_at <= dst))
        except ValueError:
            pass

    # Start from customers filtered above; find production items matching filters and return distinct customer ids
    base_cust_ids = [c.id for c in customers]
    if not base_cust_ids:
        return []

    q = db.query(func.distinct(pi.customer_id)).join(st, st.production_item_id == pi.id)
    q = q.filter(pi.customer_id.in_(base_cust_ids))
    if filters:
        q = q.filter(and_(*filters))

    matching_cust_ids = [r[0] for r in q.all()]

    # If quantity filters provided, refine via MaterialUsage
    if (quantity_min is not None) or (quantity_max is not None):
        mu_q = db.query(func.distinct(models.MaterialUsage.customer_id))
        if quantity_min is not None:
            mu_q = mu_q.filter(models.MaterialUsage.qty >= quantity_min)
        if quantity_max is not None:
            mu_q = mu_q.filter(models.MaterialUsage.qty <= quantity_max)
        mu_ids = [r[0] for r in mu_q.all()]
        matching_cust_ids = [cid for cid in matching_cust_ids if cid in mu_ids]

    selected_customers = db.query(models.Customer).filter(models.Customer.id.in_(matching_cust_ids)).all()
    out = []
    for c in selected_customers:
        out.append({"id": c.id, "name": c.name, "current_stage": compute_current_stage_for_customer(c)})
    return out


@router.get("/customers/{customer_id}", response_model=schemas.CustomerTrackingOut)
def get_customer_compat(customer_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor", "User"))):
    return get_customer_tracking(customer_id, db, current_user)


@router.put("/customers/{customer_id}/stage")
def update_customer_stage_compat(customer_id: int, payload: dict, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor"))):
    # payload expected: {stage, action, by}
    stage = payload.get('stage')
    action = payload.get('action')
    if stage not in STAGE_ORDER:
        raise HTTPException(status_code=400, detail='Invalid stage')
    cust = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not cust:
        raise HTTPException(status_code=404, detail='Customer not found')

    results = []
    # For compatibility, apply action to all eligible production items
    for item in cust.production_items:
        try:
            if action == 'started':
                # start stage when allowed
                # check previous stage completed
                idx = STAGE_ORDER.index(stage)
                if idx > 0:
                    prev = STAGE_ORDER[idx - 1]
                    prev_row = db.query(models.StageTracking).filter(models.StageTracking.production_item_id == item.id, models.StageTracking.stage == prev, models.StageTracking.status == 'completed').first()
                    if not prev_row:
                        continue
                inprog = db.query(models.StageTracking).filter(models.StageTracking.production_item_id == item.id, models.StageTracking.status == 'in_progress').first()
                if inprog:
                    continue
                row = db.query(models.StageTracking).filter(models.StageTracking.production_item_id == item.id, models.StageTracking.stage == stage).first()
                now = datetime.utcnow()
                if row:
                    row.status = 'in_progress'
                    row.started_at = now
                    row.updated_by = current_user.id
                else:
                    row = models.StageTracking(production_item_id=item.id, stage=stage, status='in_progress', started_at=now, updated_by=current_user.id)
                    db.add(row)
                db.commit()
                db.refresh(row)
                results.append(_serialize_stage(row))
            elif action == 'completed':
                row = db.query(models.StageTracking).filter(models.StageTracking.production_item_id == item.id, models.StageTracking.stage == stage).first()
                if not row or row.status != 'in_progress':
                    continue
                row.status = 'completed'
                row.completed_at = datetime.utcnow()
                row.updated_by = current_user.id
                db.commit()
                db.refresh(row)
                results.append(_serialize_stage(row))
        except Exception:
            continue
    return {"updated": len(results), "details": [r.dict() for r in results]}


@router.post("/customers/{customer_id}/material-usage", response_model=schemas.MaterialUsageOut)
def post_material_usage(customer_id: int, mu: schemas.MaterialUsageCreate, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor", "User"))):
    cust = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not cust:
        raise HTTPException(status_code=404, detail='Customer not found')
    m = models.MaterialUsage(customer_id=customer_id, production_item_id=mu.production_item_id, name=mu.name, qty=mu.qty, unit=mu.unit, by=mu.by)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m
