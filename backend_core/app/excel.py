from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from typing import List, Any
import pandas as pd
from io import BytesIO

from .deps import get_current_user

router = APIRouter(prefix="/excel", tags=["excel"])


def _to_native(value: Any):
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return value
    return value


@router.post("/upload")
async def upload_excel(file: UploadFile = File(...), current_user = Depends(get_current_user)):
    filename = (file.filename or "").lower()
    if not filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Only .xlsx Excel files are allowed")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    try:
        excel_data = pd.read_excel(BytesIO(content), sheet_name=None, engine="openpyxl")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to read Excel file: {exc}")

    if not excel_data:
        raise HTTPException(status_code=400, detail="Excel file contains no sheets")

    sheets = []
    for sheet_name, df in excel_data.items():
        cols = [str(c) for c in df.columns.tolist()]
        rows: List[List[Any]] = []
        for _, r in df.iterrows():
            row_vals = []
            for v in r.tolist():
                row_vals.append(_to_native(v))
            rows.append(row_vals)
        sheets.append({
            "sheet_name": sheet_name,
            "columns": cols,
            "rows": rows,
        })

    return {"sheets": sheets}
