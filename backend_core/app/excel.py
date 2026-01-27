from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Query
from typing import List, Any, Optional, Dict, Tuple
import pandas as pd
from io import BytesIO
from sqlalchemy.orm import Session
from sqlalchemy import or_
import json

from .deps import get_current_user, get_db, require_role
from . import models

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


# Default column mappings - maps common Excel/CSV column names to database fields
DEFAULT_COLUMN_MAPPINGS = {
    # Item Code / Drawing Number variations
    "item_code": "item_code", "item code": "item_code", "code": "item_code", 
    "sr no": "item_code", "sr.no": "item_code", "s.no": "item_code", "sno": "item_code",
    "id": "item_code", "part no": "item_code", "part_no": "item_code",
    "drawing no": "item_code", "drawing_no": "item_code", "drawing number": "item_code",
    "dwg no": "item_code", "dwg_no": "item_code", "dwg": "item_code",
    
    # Assembly / Short Code variations
    "assembly": "assembly", "assy": "assembly", "assembly no": "assembly",
    "assembly_no": "assembly", "short code": "assembly", "mark": "assembly",
    
    # Item Name variations  
    "item_name": "item_name", "item name": "item_name", "name": "item_name",
    "description": "item_name", "item": "item_name", "material": "item_name",
    "part name": "item_name", "part_name": "item_name", "product": "item_name",
    
    # Section / Profile variations
    "section": "section", "size": "section", "profile": "section",
    "type": "section", "category": "section", "grade": "section",
    
    # Length variations
    "length_mm": "length_mm", "length mm": "length_mm", "length": "length_mm",
    "length (mm)": "length_mm", "len": "length_mm", "size_mm": "length_mm",
    
    # Quantity variations
    "quantity": "quantity", "qty": "quantity", "qty.": "quantity", "count": "quantity",
    "nos": "quantity", "no": "quantity", "pcs": "quantity", "pieces": "quantity",
    
    # Unit variations
    "unit": "unit", "uom": "unit", "units": "unit",
    
    # Weight variations
    "weight_per_unit": "weight_per_unit", "weight": "weight_per_unit",
    "wt": "weight_per_unit", "wt.": "weight_per_unit", "wt-(kg)": "weight_per_unit",
    "wt (kg)": "weight_per_unit", "weight (kg)": "weight_per_unit",
    "weight/unit": "weight_per_unit", "unit weight": "weight_per_unit",
    
    # Area variations
    "area": "area", "ar": "area", "ar(m²)": "area", "ar (m²)": "area",
    "surface area": "area", "area (m2)": "area", "area_m2": "area",
    
    # Priority variations
    "priority": "priority", "location": "priority", "grid": "priority",
    
    # Date variations
    "date": "date", "issue_date": "date", "issue date": "date",
    "fab_date": "date", "fab date": "date", "fabrication date": "date",
    
    # Paint / Painting variations
    "paint": "paint_status", "painting": "paint_status", "paint status": "paint_status",
    "painted": "paint_status", "paint_status": "paint_status",
    
    # Lot / Batch variations
    "lot": "lot", "lot no": "lot", "lot_no": "lot", "batch": "lot",
    "lot 1": "lot1", "lot1": "lot1", "lot_1": "lot1",
    "lot 2": "lot2", "lot2": "lot2", "lot_2": "lot2",
    
    # Revision variations
    "rev": "revision", "revision": "revision", "rev.": "revision",
    
    # Notes variations
    "notes": "notes", "remarks": "notes", "comment": "notes", "comments": "notes",
}


def _find_column_mapping(columns: List[str]) -> dict:
    """
    Automatically detect and map Excel/CSV columns to database fields.
    Handles various column naming conventions including spaces and special chars.
    """
    mapping = {}
    for col in columns:
        col_lower = col.lower().strip()
        if col_lower in DEFAULT_COLUMN_MAPPINGS:
            db_field = DEFAULT_COLUMN_MAPPINGS[col_lower]
            if db_field not in mapping.values():  # Avoid duplicate mappings
                mapping[col] = db_field
    return mapping


def _read_file_to_dataframe(content: bytes, filename: str):
    """
    Read Excel (.xlsx) or CSV file content into pandas DataFrame(s).
    Returns dict of {sheet_name: dataframe} for consistency.
    """
    filename_lower = filename.lower()
    
    if filename_lower.endswith(".xlsx"):
        try:
            excel_data = pd.read_excel(BytesIO(content), sheet_name=None, engine="openpyxl")
            return excel_data
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to read Excel file: {exc}")
    
    elif filename_lower.endswith(".csv"):
        try:
            # Try different encodings
            for encoding in ['utf-8', 'latin-1', 'cp1252']:
                try:
                    df = pd.read_csv(BytesIO(content), encoding=encoding)
                    return {"Sheet1": df}
                except UnicodeDecodeError:
                    continue
            # Fallback
            df = pd.read_csv(BytesIO(content), encoding='utf-8', errors='ignore')
            return {"Sheet1": df}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to read CSV file: {exc}")
    
    else:
        raise HTTPException(status_code=400, detail="Only .xlsx and .csv files are supported")


def _find_inventory_by_profile(profile: str, db: Session) -> Optional[models.Inventory]:
    """
    Find matching inventory item by profile/section name.
    Uses flexible matching to handle variations in naming.
    E.g., "UB203X133X25" might match "UB 203x133x25" or "UB203*133*25"
    """
    if not profile or str(profile) == 'nan':
        return None
    
    profile_clean = str(profile).strip().upper()
    
    # Direct match first
    inv = db.query(models.Inventory).filter(
        or_(
            models.Inventory.name.ilike(profile_clean),
            models.Inventory.section.ilike(profile_clean) if hasattr(models.Inventory, 'section') else False,
            models.Inventory.code.ilike(profile_clean) if hasattr(models.Inventory, 'code') else False
        )
    ).first()
    
    if inv:
        return inv
    
    # Fuzzy match - remove separators and try partial match
    profile_normalized = profile_clean.replace('X', '*').replace('x', '*').replace(' ', '').replace('-', '')
    
    all_inventory = db.query(models.Inventory).all()
    for inv in all_inventory:
        inv_name_normalized = (inv.name or '').upper().replace('X', '*').replace('x', '*').replace(' ', '').replace('-', '')
        if profile_normalized in inv_name_normalized or inv_name_normalized in profile_normalized:
            return inv
        
        if hasattr(inv, 'section') and inv.section:
            inv_section_normalized = inv.section.upper().replace('X', '*').replace('x', '*').replace(' ', '').replace('-', '')
            if profile_normalized in inv_section_normalized or inv_section_normalized in profile_normalized:
                return inv
    
    return None


def _validate_and_link_materials(
    df: pd.DataFrame, 
    field_to_col: Dict[str, str], 
    db: Session
) -> Tuple[List[Dict], List[str], List[str]]:
    """
    Validate that PROFILE/Section values exist in inventory and prepare material links.
    
    Returns:
        - material_links: List of {row_idx, profile, inventory_id, inventory_name, weight, qty, total_weight}
        - matched_profiles: List of profile names that matched inventory
        - unmatched_profiles: List of profile names that didn't match inventory
    """
    material_links = []
    matched_profiles = []
    unmatched_profiles = []
    seen_profiles = set()
    
    for idx, row in df.iterrows():
        # Get section/profile value
        section = _to_native(row.get(field_to_col.get('section', ''), None))
        if not section or str(section) == 'nan':
            continue
            
        section_str = str(section).strip()
        
        # Get weight and quantity for material calculation
        weight = _to_native(row.get(field_to_col.get('weight_per_unit', ''), None))
        quantity = _to_native(row.get(field_to_col.get('quantity', ''), 1))
        
        try:
            weight = float(weight) if weight and str(weight) != 'nan' else 0
        except:
            weight = 0
            
        try:
            quantity = float(quantity) if quantity and str(quantity) != 'nan' else 1
        except:
            quantity = 1
        
        # Find matching inventory
        inv = _find_inventory_by_profile(section_str, db)
        
        link = {
            "row_idx": idx,
            "profile": section_str,
            "weight": weight,
            "qty": quantity,
            "total_weight": weight * quantity,
        }
        
        if inv:
            link["inventory_id"] = inv.id
            link["inventory_name"] = inv.name
            link["inventory_available"] = (inv.total or 0) - (inv.used or 0)
            if section_str not in seen_profiles:
                matched_profiles.append(section_str)
                seen_profiles.add(section_str)
        else:
            link["inventory_id"] = None
            link["inventory_name"] = None
            link["inventory_available"] = 0
            if section_str not in seen_profiles:
                unmatched_profiles.append(section_str)
                seen_profiles.add(section_str)
        
        material_links.append(link)
    
    return material_links, matched_profiles, unmatched_profiles


@router.post("/upload")
async def upload_excel(file: UploadFile = File(...), current_user = Depends(get_current_user)):
    """Upload and preview Excel/CSV file contents without importing."""
    filename = (file.filename or "").lower()
    if not (filename.endswith(".xlsx") or filename.endswith(".csv")):
        raise HTTPException(status_code=400, detail="Only .xlsx and .csv files are supported")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    file_data = _read_file_to_dataframe(content, filename)

    if not file_data:
        raise HTTPException(status_code=400, detail="File contains no data")

    sheets = []
    for sheet_name, df in file_data.items():
        cols = [str(c).strip() for c in df.columns.tolist()]
        rows: List[List[Any]] = []
        for _, r in df.iterrows():
            row_vals = []
            for v in r.tolist():
                row_vals.append(_to_native(v))
            rows.append(row_vals)
        
        # Auto-detect column mapping
        detected_mapping = _find_column_mapping(cols)
        
        sheets.append({
            "sheet_name": sheet_name,
            "columns": cols,
            "rows": rows,
            "detected_mapping": detected_mapping,
            "row_count": len(rows),
        })

    return {"sheets": sheets}


@router.post("/import-tracking/{customer_id}")
async def import_tracking_excel(
    customer_id: int,
    file: UploadFile = File(...),
    sheet_name: Optional[str] = Query(None, description="Specific sheet to import, or None for first sheet"),
    column_mapping: Optional[str] = Query(None, description="JSON string of custom column mapping"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("Boss", "Software Supervisor")),
):
    """
    Import Excel file as tracking items for a customer.
    
    This endpoint:
    1. Reads the Excel file (supports various formats/columns)
    2. Auto-detects column mappings or uses provided custom mapping
    3. Creates production items for the customer
    4. Initializes all items at Fabrication stage
    
    The system handles flexible Excel formats - columns are mapped automatically
    based on common naming patterns (e.g., "Qty", "quantity", "nos" all map to quantity).
    """
    import json as json_module
    
    # Verify customer exists
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    filename = (file.filename or "").lower()
    if not (filename.endswith(".xlsx") or filename.endswith(".csv")):
        raise HTTPException(status_code=400, detail="Only .xlsx and .csv files are supported")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    file_data = _read_file_to_dataframe(content, filename)

    if not file_data:
        raise HTTPException(status_code=400, detail="File contains no data")

    # Select sheet to import
    if sheet_name and sheet_name in file_data:
        df = file_data[sheet_name]
    else:
        # Use first sheet
        df = list(file_data.values())[0]
    
    cols = [str(c).strip() for c in df.columns.tolist()]
    
    # Determine column mapping
    if column_mapping:
        try:
            mapping = json_module.loads(column_mapping)
        except:
            mapping = _find_column_mapping(cols)
    else:
        mapping = _find_column_mapping(cols)
    
    if not mapping:
        raise HTTPException(
            status_code=400, 
            detail="Could not detect column mapping. Please ensure columns have recognizable names or provide custom mapping."
        )
    
    # Reverse mapping for easy lookup
    field_to_col = {v: k for k, v in mapping.items()}
    
    # STEP 1: Validate and link materials BEFORE creating items
    material_links, matched_profiles, unmatched_profiles = _validate_and_link_materials(df, field_to_col, db)
    
    # Create lookup dict for quick access
    material_link_by_row = {link["row_idx"]: link for link in material_links}
    
    # STEP 2: Load existing items for this customer for DEDUPLICATION
    existing_items = db.query(models.ProductionItem).filter(
        models.ProductionItem.customer_id == customer_id
    ).all()
    existing_codes = {item.item_code.lower(): item for item in existing_items if item.item_code}
    existing_names = {item.item_name.lower(): item for item in existing_items if item.item_name}
    
    items_created = 0
    items_updated = 0
    items_skipped = 0
    items_with_material_link = 0
    errors = []
    
    for idx, row in df.iterrows():
        try:
            # Extract values using mapping
            item_code = str(_to_native(row.get(field_to_col.get('item_code', ''), idx + 1)))
            item_name = str(_to_native(row.get(field_to_col.get('item_name', ''), f"Item {idx + 1}")))
            
            # Skip empty rows
            if not item_name or item_name == 'nan' or item_name == 'None':
                continue
            
            # DEDUPLICATION: Check if item already exists by code or name
            existing_item = None
            if item_code and item_code.lower() in existing_codes:
                existing_item = existing_codes[item_code.lower()]
            elif item_name.lower() in existing_names:
                existing_item = existing_names[item_name.lower()]
            
            # If item exists and fabrication already deducted, skip entirely
            if existing_item and existing_item.fabrication_deducted:
                items_skipped += 1
                continue
            
            section = _to_native(row.get(field_to_col.get('section', ''), None))
            length_mm = _to_native(row.get(field_to_col.get('length_mm', ''), None))
            quantity = _to_native(row.get(field_to_col.get('quantity', ''), 1))
            unit = _to_native(row.get(field_to_col.get('unit', ''), None))
            weight = _to_native(row.get(field_to_col.get('weight_per_unit', ''), None))
            notes = _to_native(row.get(field_to_col.get('notes', ''), None))
            
            # Convert types
            try:
                length_mm = int(length_mm) if length_mm and str(length_mm) != 'nan' else None
            except:
                length_mm = None
            
            try:
                quantity = float(quantity) if quantity and str(quantity) != 'nan' else 1.0
            except:
                quantity = 1.0
            
            try:
                weight = float(weight) if weight and str(weight) != 'nan' else None
            except:
                weight = None
            
            # AUTO-LINK: Build material_requirements from validated profile matching
            material_requirements = None
            mat_link = material_link_by_row.get(idx)
            if mat_link and mat_link.get("inventory_id"):
                # Link to inventory item with calculated quantity (weight * qty)
                total_weight = mat_link.get("total_weight", 0)
                if total_weight > 0:
                    material_requirements = json.dumps([{
                        "material_id": mat_link["inventory_id"],
                        "qty": total_weight,
                        "profile": mat_link["profile"],
                        "inventory_name": mat_link["inventory_name"]
                    }])
                    items_with_material_link += 1
            
            # UPSERT LOGIC: Update existing item OR create new one
            if existing_item:
                # UPDATE existing item (preserving stage progress)
                existing_item.section = str(section) if section and str(section) != 'nan' else existing_item.section
                existing_item.length_mm = length_mm if length_mm else existing_item.length_mm
                existing_item.quantity = quantity
                existing_item.unit = str(unit) if unit and str(unit) != 'nan' else existing_item.unit
                existing_item.weight_per_unit = weight if weight else existing_item.weight_per_unit
                existing_item.notes = str(notes) if notes and str(notes) != 'nan' else existing_item.notes
                # Only update material_requirements if not already deducted
                if material_requirements and not existing_item.fabrication_deducted:
                    existing_item.material_requirements = material_requirements
                db.add(existing_item)
                items_updated += 1
            else:
                # CREATE new production item
                item = models.ProductionItem(
                    customer_id=customer_id,
                    item_code=item_code if item_code and item_code != 'nan' else f"ITEM-{idx + 1}",
                    item_name=item_name,
                    section=str(section) if section and str(section) != 'nan' else None,
                    length_mm=length_mm,
                    quantity=quantity,
                    unit=str(unit) if unit and str(unit) != 'nan' else None,
                    weight_per_unit=weight,
                    material_requirements=material_requirements,  # AUTO-LINKED!
                    notes=str(notes) if notes and str(notes) != 'nan' else None,
                    fabrication_deducted=False,
                )
                db.add(item)
                db.flush()  # Get the item ID
                
                # Initialize at Fabrication stage (all items start here)
                from datetime import datetime
                stage = models.StageTracking(
                    production_item_id=item.id,
                    stage="fabrication",
                    status="pending",
                    updated_by=current_user.id,
                )
                db.add(stage)
                
                # Add to lookup to prevent duplicates within same import
                existing_codes[item.item_code.lower()] = item
                existing_names[item.item_name.lower()] = item
                
                items_created += 1
            
        except Exception as e:
            errors.append(f"Row {idx + 1}: {str(e)}")
    
    # Create notification for unmatched profiles (admin should add these to inventory)
    if unmatched_profiles:
        notification = models.Notification(
            user_id=current_user.id,
            role="Boss",
            message=f"⚠️ Import for '{customer.name}': {len(unmatched_profiles)} profiles not found in inventory: {', '.join(unmatched_profiles[:5])}{'...' if len(unmatched_profiles) > 5 else ''}. Add these to Raw Materials for auto-deduction.",
            level="warning"
        )
        db.add(notification)
    
    db.commit()
    
    # Build summary message
    summary_parts = []
    if items_created > 0:
        summary_parts.append(f"{items_created} new items created")
    if items_updated > 0:
        summary_parts.append(f"{items_updated} existing items updated")
    if items_skipped > 0:
        summary_parts.append(f"{items_skipped} completed items skipped")
    
    summary_msg = ", ".join(summary_parts) if summary_parts else "No items processed"
    
    return {
        "message": f"Import complete for '{customer.name}': {summary_msg}",
        "items_created": items_created,
        "items_updated": items_updated,
        "items_skipped": items_skipped,
        "items_with_material_link": items_with_material_link,
        "customer_id": customer_id,
        "customer_name": customer.name,
        "column_mapping_used": mapping,
        "material_matching": {
            "matched_profiles": matched_profiles,
            "unmatched_profiles": unmatched_profiles,
            "note": "Unmatched profiles will NOT auto-deduct from inventory. Add them to Raw Materials first." if unmatched_profiles else "All profiles matched to inventory!"
        },
        "errors": errors if errors else None,
    }


@router.post("/preview-import/{customer_id}")
async def preview_import_excel(
    customer_id: int,
    file: UploadFile = File(...),
    sheet_name: Optional[str] = Query(None, description="Specific sheet to preview"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("Boss", "Software Supervisor")),
):
    """
    Preview Excel/CSV file BEFORE importing - shows material matching status.
    
    This helps admin see which PROFILEs match inventory (for auto-deduction)
    and which need to be added to Raw Materials first.
    """
    import json as json_module
    
    # Verify customer exists
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    filename = (file.filename or "").lower()
    if not (filename.endswith(".xlsx") or filename.endswith(".csv")):
        raise HTTPException(status_code=400, detail="Only .xlsx and .csv files are supported")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    file_data = _read_file_to_dataframe(content, filename)

    if not file_data:
        raise HTTPException(status_code=400, detail="File contains no data")

    # Select sheet to preview
    if sheet_name and sheet_name in file_data:
        df = file_data[sheet_name]
        selected_sheet = sheet_name
    else:
        df = list(file_data.values())[0]
        selected_sheet = list(file_data.keys())[0]
    
    cols = [str(c).strip() for c in df.columns.tolist()]
    mapping = _find_column_mapping(cols)
    field_to_col = {v: k for k, v in mapping.items()}
    
    # Validate and link materials
    material_links, matched_profiles, unmatched_profiles = _validate_and_link_materials(df, field_to_col, db)
    
    # Calculate totals
    total_weight_matched = sum(link["total_weight"] for link in material_links if link.get("inventory_id"))
    total_weight_unmatched = sum(link["total_weight"] for link in material_links if not link.get("inventory_id"))
    
    # Build preview rows (first 15)
    preview_rows = []
    for idx, row in df.head(15).iterrows():
        row_data = {}
        for col in cols:
            row_data[col] = _to_native(row.get(col))
        
        # Add material match status
        mat_link = next((l for l in material_links if l["row_idx"] == idx), None)
        if mat_link:
            row_data["__material_status"] = "✅ Matched" if mat_link.get("inventory_id") else "⚠️ Not Found"
            row_data["__inventory_name"] = mat_link.get("inventory_name", "-")
            row_data["__total_weight"] = mat_link.get("total_weight", 0)
        
        preview_rows.append(row_data)
    
    return {
        "customer": {"id": customer.id, "name": customer.name},
        "file_info": {
            "sheets": list(file_data.keys()),
            "selected_sheet": selected_sheet,
            "total_rows": len(df),
            "columns": cols,
        },
        "column_mapping": mapping,
        "material_matching": {
            "matched_profiles": matched_profiles,
            "matched_count": len(matched_profiles),
            "unmatched_profiles": unmatched_profiles,
            "unmatched_count": len(unmatched_profiles),
            "total_weight_matched_kg": round(total_weight_matched, 2),
            "total_weight_unmatched_kg": round(total_weight_unmatched, 2),
            "all_matched": len(unmatched_profiles) == 0,
            "warning": f"⚠️ {len(unmatched_profiles)} profiles not in inventory - add to Raw Materials for auto-deduction" if unmatched_profiles else None,
        },
        "preview_rows": preview_rows,
        "ready_to_import": True,
        "instructions": [
            "✅ Matched profiles will auto-deduct from inventory when Fabrication completes",
            "⚠️ Unmatched profiles need to be added to Raw Materials first",
            "Items can still be imported - unmatched items won't auto-deduct",
        ]
    }


@router.get("/template")
async def get_excel_template(current_user = Depends(get_current_user)):
    """
    Returns information about the expected Excel format.
    The system is flexible and can handle various column names.
    """
    return {
        "message": "Excel template information",
        "supported_columns": {
            "item_code": ["Item Code", "Code", "Sr No", "S.No", "Part No", "ID"],
            "item_name": ["Item Name", "Name", "Description", "Material", "Part Name", "Product"],
            "section": ["Section", "Size", "Profile", "Type", "Category", "Grade"],
            "length_mm": ["Length (mm)", "Length", "Len", "Size_mm"],
            "quantity": ["Quantity", "Qty", "Count", "Nos", "Pcs", "Pieces"],
            "unit": ["Unit", "UOM", "Units"],
            "weight_per_unit": ["Weight", "Wt", "Weight/Unit", "Unit Weight"],
            "notes": ["Notes", "Remarks", "Comments"],
        },
        "example_format": {
            "columns": ["Sr No", "Item Name", "Section", "Length (mm)", "Qty", "Unit", "Remarks"],
            "sample_row": ["1", "Steel Beam", "IPE 200", "6000", "10", "Pcs", "Main structure"]
        },
        "notes": [
            "The system automatically detects column names",
            "Column order doesn't matter",
            "Not all columns are required - minimum is Item Name",
            "Multiple sheets are supported - specify sheet_name parameter to select",
            "Both .xlsx and .csv files are supported",
        ]
    }


@router.post("/upload-stage/{stage}")
async def upload_stage_excel(
    stage: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("Boss", "Software Supervisor")),
):
    """
    Upload Excel/CSV file to update items at a specific stage.
    
    Stage can be: fabrication, painting, dispatch
    
    The file should contain item identifiers (Drawing no, item_code, Assembly, or item_name) to match existing items.
    Additional columns can include stage-specific data like completion status, notes, etc.
    
    Supports columns like: Drawing no, ASSEMBLY, NAME, PROFILE, QTY., WT-(kg), AR(m²), PRIORITY, PAINT, DATE, LOT
    """
    from datetime import datetime
    
    stage = stage.lower()
    valid_stages = ["fabrication", "painting", "dispatch"]
    if stage not in valid_stages:
        raise HTTPException(status_code=400, detail=f"Invalid stage. Must be one of: {valid_stages}")
    
    filename = (file.filename or "").lower()
    if not (filename.endswith(".xlsx") or filename.endswith(".csv")):
        raise HTTPException(status_code=400, detail="Only .xlsx and .csv files are supported")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    file_data = _read_file_to_dataframe(content, filename)

    if not file_data:
        raise HTTPException(status_code=400, detail="File contains no data")

    # Use first sheet
    df = list(file_data.values())[0]
    cols = [str(c).strip() for c in df.columns.tolist()]
    
    # Extended column mappings for stage data
    stage_column_mappings = {
        **DEFAULT_COLUMN_MAPPINGS,
        # Status variations
        "status": "status", "stage_status": "status", "completion": "status",
        "completed": "status", "done": "status", "state": "status",
        # Quantity completed variations
        "qty_completed": "qty_completed", "completed_qty": "qty_completed",
        "done_qty": "qty_completed", "finished": "qty_completed",
        # Stage notes
        "stage_notes": "stage_notes", "stage_remarks": "stage_notes",
        "work_notes": "stage_notes", "completion_notes": "stage_notes",
    }
    
    # Find column mapping
    mapping = {}
    for col in cols:
        col_lower = col.lower().strip()
        if col_lower in stage_column_mappings:
            db_field = stage_column_mappings[col_lower]
            if db_field not in mapping.values():
                mapping[col] = db_field
    
    field_to_col = {v: k for k, v in mapping.items()}
    
    items_updated = 0
    items_not_found = []
    errors = []
    stage_updates = []
    
    for idx, row in df.iterrows():
        try:
            # Get item identifier
            item_code = _to_native(row.get(field_to_col.get('item_code', ''), None))
            item_name = _to_native(row.get(field_to_col.get('item_name', ''), None))
            
            # Skip empty rows
            if (not item_code or str(item_code) == 'nan') and (not item_name or str(item_name) == 'nan'):
                continue
            
            # Find matching production item
            query = db.query(models.ProductionItem)
            if item_code and str(item_code) != 'nan':
                query = query.filter(models.ProductionItem.item_code == str(item_code))
            elif item_name and str(item_name) != 'nan':
                query = query.filter(models.ProductionItem.item_name == str(item_name))
            
            item = query.first()
            
            if not item:
                items_not_found.append(f"Row {idx + 1}: {item_code or item_name}")
                continue
            
            # Get or create stage tracking
            stage_tracking = db.query(models.StageTracking).filter(
                models.StageTracking.production_item_id == item.id,
                models.StageTracking.stage == stage
            ).first()
            
            if not stage_tracking:
                stage_tracking = models.StageTracking(
                    production_item_id=item.id,
                    stage=stage,
                    status="pending"
                )
                db.add(stage_tracking)
            
            # Update stage status if provided
            status = _to_native(row.get(field_to_col.get('status', ''), None))
            if status and str(status) != 'nan':
                status_str = str(status).lower()
                if status_str in ['completed', 'done', 'yes', '1', 'true', 'complete']:
                    stage_tracking.status = 'completed'
                    stage_tracking.completed_at = datetime.utcnow()
                elif status_str in ['in_progress', 'in progress', 'wip', 'working', 'started']:
                    stage_tracking.status = 'in_progress'
                    if not stage_tracking.started_at:
                        stage_tracking.started_at = datetime.utcnow()
                else:
                    stage_tracking.status = 'pending'
            
            stage_tracking.updated_by = current_user.id
            
            # Update item notes if provided
            stage_notes = _to_native(row.get(field_to_col.get('stage_notes', ''), None))
            if stage_notes and str(stage_notes) != 'nan':
                existing_notes = item.notes or ''
                item.notes = f"{existing_notes}\n[{stage.capitalize()}]: {stage_notes}".strip()
            
            # Update quantity if provided
            quantity = _to_native(row.get(field_to_col.get('quantity', ''), None))
            if quantity and str(quantity) != 'nan':
                try:
                    item.quantity = float(quantity)
                except:
                    pass
            
            # Update current_stage if this stage is being marked as completed and it's the current stage
            if hasattr(item, 'current_stage') and stage_tracking.status == 'completed':
                next_stage_map = {"fabrication": "painting", "painting": "dispatch", "dispatch": None}
                next_stage = next_stage_map.get(stage)
                if next_stage and item.current_stage == stage:
                    item.current_stage = next_stage
                    item.stage_updated_at = datetime.utcnow()
                    item.stage_updated_by = current_user.id
            
            db.add(stage_tracking)
            db.add(item)
            items_updated += 1
            
            stage_updates.append({
                "item_code": item.item_code,
                "item_name": item.item_name,
                "stage": stage,
                "status": stage_tracking.status,
            })
            
        except Exception as e:
            errors.append(f"Row {idx + 1}: {str(e)}")
    
    db.commit()
    
    return {
        "message": f"Stage '{stage}' Excel processed: {items_updated} items updated",
        "stage": stage,
        "items_updated": items_updated,
        "items_not_found": items_not_found if items_not_found else None,
        "column_mapping_used": mapping,
        "updates": stage_updates[:20],  # First 20 updates as preview
        "errors": errors if errors else None,
    }


@router.post("/preview-stage/{stage}")
async def preview_stage_excel(
    stage: str,
    file: UploadFile = File(...),
    current_user: models.User = Depends(require_role("Boss", "Software Supervisor")),
):
    """
    Preview Excel/CSV file for stage upload without actually importing.
    Shows matched items and detected columns.
    
    Supports both .xlsx and .csv files.
    """
    stage = stage.lower()
    valid_stages = ["fabrication", "painting", "dispatch"]
    if stage not in valid_stages:
        raise HTTPException(status_code=400, detail=f"Invalid stage. Must be one of: {valid_stages}")
    
    filename = (file.filename or "").lower()
    if not (filename.endswith(".xlsx") or filename.endswith(".csv")):
        raise HTTPException(status_code=400, detail="Only .xlsx and .csv files are supported")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    file_data = _read_file_to_dataframe(content, filename)

    if not file_data:
        raise HTTPException(status_code=400, detail="File contains no data")

    # Use first sheet
    df = list(file_data.values())[0]
    cols = [str(c).strip() for c in df.columns.tolist()]
    
    # Find column mapping
    mapping = _find_column_mapping(cols)
    
    # Convert rows to list for preview
    rows = []
    for _, r in df.head(10).iterrows():
        row_vals = {}
        for col in cols:
            row_vals[col] = _to_native(r.get(col))
        rows.append(row_vals)
    
    return {
        "stage": stage,
        "sheet_name": list(file_data.keys())[0],
        "columns": cols,
        "detected_mapping": mapping,
        "row_count": len(df),
        "preview_rows": rows,
        "instructions": f"This will update items at the '{stage.capitalize()}' stage. Items are matched by Drawing No, Item Code, Assembly, or Item Name.",
    }
