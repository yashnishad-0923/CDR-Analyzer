
from fastapi import APIRouter, UploadFile, File, HTTPException, Response, Depends, Form
from fastapi.responses import StreamingResponse

from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
import io
import csv

from models.database import get_db, Case
from models.schema import CaseCreate, CaseResponse, EvidenceLogResponse
from services.ingestion import (
    ingest_cdr_file, get_all_cdrs,
    ingest_tower_file, log_action,
)
from services.analysis import (
    get_custody_log, get_anomalies, get_behavior_profile,
    execute_query,
    get_unified_timeline, get_graph_metrics, get_movement, get_case_summary,
    get_entity_details, get_imei_details,
    get_cross_analysis, get_imei_graph, delete_case,
    list_case_cells, get_cell_activity,
)
from services.report import generate_pdf_report

router = APIRouter()


# ---------------------------------------------------------------------------
# Case management
# ---------------------------------------------------------------------------

@router.post("/cases", response_model=CaseResponse)
def create_case(case: CaseCreate, db: Session = Depends(get_db)):
    db_case = Case(**case.dict())
    db.add(db_case)
    db.commit()
    db.refresh(db_case)
    return db_case


@router.get("/cases", response_model=List[CaseResponse])
def list_cases(db: Session = Depends(get_db)):
    return db.query(Case).order_by(Case.created_at.desc()).all()


@router.get("/cases/{case_id}", response_model=CaseResponse)
def get_case(case_id: int, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.get("/cases/{case_id}/summary")
def case_summary(case_id: int, db: Session = Depends(get_db)):
    return get_case_summary(db, case_id)


@router.delete("/cases/{case_id}")
def remove_case(case_id: int, db: Session = Depends(get_db)):
    """Permanently delete a case and all its CDRs, towers, anomalies and logs."""
    result = delete_case(db, case_id)
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail=result.get("error", "Case not found"))
    return result


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def _require_csv(file: UploadFile):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported for now.")


@router.post("/upload/cdr")
async def upload_cdr(
    case_id: int = Form(...),
    timezone: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    _require_csv(file)
    content = await file.read()
    try:
        return ingest_cdr_file(db, case_id, file.filename, content, timezone)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse CSV: {e}")




@router.post("/upload/towers")
async def upload_towers(
    case_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload cell-tower reference data: cell_id, latitude, longitude[, operator, address]."""
    _require_csv(file)
    content = await file.read()
    try:
        return ingest_tower_file(db, case_id, file.filename, content)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse CSV: {e}")


# ---------------------------------------------------------------------------
# Record listing
# ---------------------------------------------------------------------------

@router.get("/cdrs")
async def list_cdrs(case_id: int, start_date: str = None, end_date: str = None, db: Session = Depends(get_db)):
    from services.enrichment import operator_from_imsi
    cdrs = get_all_cdrs(db, case_id, start_date, end_date)
    out = []
    for r in cdrs:
        op = r.operator or (operator_from_imsi(r.imsi).get("operator") if r.imsi else None)
        out.append({
            "id": r.id,
            "subject_id": r.subject_id,
            "event_type": r.event_type,
            "direction": r.direction,
            "caller": r.caller,
            "callee": r.callee,
            "start_time": r.start_time.isoformat() if r.start_time else None,
            "end_time": r.end_time.isoformat() if r.end_time else None,
            "normalized_time": r.normalized_time.isoformat() if r.normalized_time else None,
            "duration": r.duration,
            "cell_id": r.cell_id,
            "last_cell_id": r.last_cell_id,
            "imei": r.imei,
            "imsi": r.imsi,
            "operator": op or "Unknown",
            "roaming_center": r.roaming_center,
        })
    return {"cdrs": out, "count": len(out)}




# ---------------------------------------------------------------------------
# Reports & export
# ---------------------------------------------------------------------------

@router.get("/report/pdf")
async def get_pdf_report(case_id: int, start_date: str = None, end_date: str = None, db: Session = Depends(get_db)):
    pdf_buffer = generate_pdf_report(db, case_id, start_date, end_date)
    return Response(
        content=pdf_buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=case_{case_id}_cdr_report.pdf"},
    )


class ReportVisual(BaseModel):
    title: Optional[str] = "Figure"
    caption: Optional[str] = ""
    image: str  # data-URI PNG captured from the browser


class ReportRequest(BaseModel):
    case_id: int
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    sections: Optional[Dict[str, bool]] = None
    visuals: Optional[List[ReportVisual]] = None


@router.post("/report/pdf")
async def post_pdf_report(req: ReportRequest, db: Session = Depends(get_db)):
    """Generate a customized PDF: caller chooses which sections to include and
    supplies browser-captured graph/map images to embed."""
    visuals = [v.dict() for v in (req.visuals or [])]
    pdf_buffer = generate_pdf_report(
        db, req.case_id, req.start_date, req.end_date,
        sections=req.sections, visuals=visuals,
    )
    return Response(
        content=pdf_buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=case_{req.case_id}_cdr_report.pdf"},
    )




@router.get("/export/csv")
async def export_csv(case_id: int, data_type: str = "cdr", db: Session = Depends(get_db)):
    """Export the full normalized dataset as CSV (logged in chain of custody)."""
    output = io.StringIO()
    writer = csv.writer(output)

    rows = get_all_cdrs(db, case_id)
    writer.writerow(["id", "subject_id", "event_type", "caller", "callee",
                     "start_time", "normalized_time_utc", "duration", "cell_id",
                     "imei", "imsi", "operator"])
    for r in rows:
        writer.writerow([r.id, r.subject_id, r.event_type, r.caller, r.callee,
                         r.start_time, r.normalized_time, r.duration, r.cell_id,
                         r.imei, r.imsi, r.operator])

    log_action(db, case_id, "exported", file_name=f"case_{case_id}_{data_type}_export.csv",
               record_count=len(rows))

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=case_{case_id}_{data_type}_export.csv"},
    )


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

@router.get("/case/{case_id}/custody-log", response_model=List[EvidenceLogResponse])
def case_custody_log(case_id: int, db: Session = Depends(get_db)):
    return get_custody_log(db, case_id)


@router.get("/case/{case_id}/anomalies")
def case_anomalies(case_id: int, db: Session = Depends(get_db)):
    return get_anomalies(db, case_id)


@router.get("/case/{case_id}/subject/{subject_id}/profile")
def subject_profile(case_id: int, subject_id: str, db: Session = Depends(get_db)):
    return get_behavior_profile(db, case_id, subject_id)





@router.get("/case/{case_id}/subject/{subject_id}/movement")
def subject_movement(case_id: int, subject_id: str, db: Session = Depends(get_db)):
    return get_movement(db, case_id, subject_id)


@router.post("/case/{case_id}/geolocate-towers")
def geolocate_towers(case_id: int, use_opencellid: bool = True, db: Session = Depends(get_db)):
    """Resolve lat/lon for every cell_id in the case (OpenCellID + roaming-centre
    fallback) and cache the results. Safe to call repeatedly."""
    from services.geolocation import resolve_case_towers
    stats = resolve_case_towers(db, case_id, use_opencellid=use_opencellid)
    return stats


@router.get("/case/{case_id}/cells")
def case_cells(case_id: int, db: Session = Depends(get_db)):
    """List every cell tower seen in the case (for the cell-activity picker)."""
    return list_case_cells(db, case_id)


@router.get("/case/{case_id}/cell/{cell_id}/activity")
def cell_activity(case_id: int, cell_id: str, db: Session = Depends(get_db)):
    """Which numbers were active on a given cell, and which other cells they
    connect to (handovers + co-used cells)."""
    return get_cell_activity(db, case_id, cell_id)


@router.get("/case/{case_id}/entity/{number}/details")
def entity_details(case_id: int, number: str, db: Session = Depends(get_db)):
    """Drill-down details for one phone number (used by graph node clicks)."""
    return get_entity_details(db, case_id, number)


@router.get("/case/{case_id}/imei/{imei}/details")
def imei_details_route(case_id: int, imei: str, db: Session = Depends(get_db)):
    """Handset details for an IMEI plus which subjects/SIMs used it."""
    return get_imei_details(db, case_id, imei)


@router.get("/case/{case_id}/cross-analysis")
def cross_analysis(case_id: int, top_n: int = 25, db: Session = Depends(get_db)):
    """Insights across all CDRs in a case: common numbers, shared handsets/SIMs,
    shared cells, busiest talkers."""
    return get_cross_analysis(db, case_id, top_n)


@router.get("/case/{case_id}/imei-graph")
def imei_graph(case_id: int, db: Session = Depends(get_db)):
    """Number <-> handset (IMEI) graph highlighting SIM-swap clusters."""
    return get_imei_graph(db, case_id)


@router.get("/case/{case_id}/timeline")
def case_timeline(case_id: int, subject_id: Optional[str] = None, limit: int = 500, db: Session = Depends(get_db)):
    return get_unified_timeline(db, case_id, subject_id, limit)


@router.get("/case/{case_id}/graph-metrics")
def case_graph_metrics(case_id: int, data_type: str = "cdr", db: Session = Depends(get_db)):
    return get_graph_metrics(db, case_id, data_type)


class QueryRequest(BaseModel):
    subject_id: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


@router.post("/case/{case_id}/query")
def subject_query(case_id: int, req: QueryRequest, db: Session = Depends(get_db)):
    return execute_query(db, case_id, req.dict())
