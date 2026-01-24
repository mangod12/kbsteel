from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from . import models, schemas
from .deps import get_db, require_role

router = APIRouter(prefix="/instructions", tags=["instructions"])


@router.post("", response_model=schemas.InstructionOut, status_code=201)
def post_instruction(instr_in: schemas.InstructionCreate, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss"))):
    instr = models.Instruction(message=instr_in.message, created_by=current_user.id)
    db.add(instr)
    db.commit()
    db.refresh(instr)
    return instr


@router.get("", response_model=List[schemas.InstructionOut])
def list_instructions(db: Session = Depends(get_db), current_user: models.User = Depends(require_role("Boss", "Software Supervisor", "User"))):
    items = db.query(models.Instruction).all()
    return items
