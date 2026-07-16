
from fastapi import APIRouter, UploadFile, File, HTTPException, Response, Depends, Form

from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional

from models.database import get_db, Case
from models.schema import CaseCreate, CaseResponse, EvidenceLogResponse
from services.ingestion import ingest_cdr_file, get_all_cdrs, ingest_ipdr_file, get_all_ipdrs

router = APIRouter()

@router.post("/cases", response_model=CaseResponse)
def create_case(case: CaseCreate, db: Session = Depends(get_db)):
    db_case = Case(**case.dict())
    db.add(db_case)
    db.commit()
    db.refresh(db_case)
    return db_case

@router.get("/cases", response_model=List[CaseResponse])
def list_cases(db: Session = Depends(get_db)):
    return db.query(Case).all()

@router.get("/cases/{case_id}", response_model=CaseResponse)
def get_case(case_id: int, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case

@router.post("/upload/cdr")
async def upload_cdr(
    case_id: int = Form(...),
    timezone: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are supported for now.")
    content = await file.read()
    # ingest_cdr_file will need to accept db, case_id, timezone, file_name, content
    return ingest_cdr_file(db, case_id, file.filename, content, timezone)

@router.get("/cdrs")
async def list_cdrs(case_id: int, start_date: str = None, end_date: str = None, db: Session = Depends(get_db)):
    cdrs = get_all_cdrs(db, case_id, start_date, end_date)
    return {"cdrs": cdrs, "count": len(cdrs)}

@router.post("/upload/ipdr")
async def upload_ipdr(
    case_id: int = Form(...),
    timezone: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are supported for now.")
    content = await file.read()
    return ingest_ipdr_file(db, case_id, file.filename, content, timezone)

@router.get("/ipdrs")
async def list_ipdrs(case_id: int, start_date: str = None, end_date: str = None, db: Session = Depends(get_db)):
    ipdrs = get_all_ipdrs(db, case_id, start_date, end_date)
    return {"ipdrs": ipdrs, "count": len(ipdrs)}

# Reporting routes
from services.report import generate_pdf_report, generate_ipdr_pdf_report

@router.get("/report/pdf")
async def get_pdf_report(case_id: int, start_date: str = None, end_date: str = None, db: Session = Depends(get_db)):
    pdf_buffer = generate_pdf_report(db, case_id, start_date, end_date)
    return Response(
        content=pdf_buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=case_{case_id}_cdr_report.pdf"}
    )

@router.get("/report/ipdr/pdf")
async def get_ipdr_pdf_report(case_id: int, start_date: str = None, end_date: str = None, db: Session = Depends(get_db)):
    pdf_buffer = generate_ipdr_pdf_report(db, case_id, start_date, end_date)
    return Response(
        content=pdf_buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=case_{case_id}_ipdr_report.pdf"}
    )

# Analysis routes
from services.analysis import (
    get_custody_log, get_anomalies, get_behavior_profile,
    get_cross_correlation, get_common_contacts, execute_query
)
from pydantic import BaseModel

@router.get("/case/{case_id}/custody-log", response_model=List[EvidenceLogResponse])
def case_custody_log(case_id: int, db: Session = Depends(get_db)):
    return get_custody_log(db, case_id)

@router.get("/case/{case_id}/anomalies")
def case_anomalies(case_id: int, db: Session = Depends(get_db)):
    # Returns list of dicts instead of AnomalyResponse due to pydantic versioning ease
    return get_anomalies(db, case_id)

@router.get("/case/{case_id}/subject/{subject_id}/profile")
def subject_profile(case_id: int, subject_id: str, db: Session = Depends(get_db)):
    return get_behavior_profile(db, case_id, subject_id)

@router.get("/case/{case_id}/subject/{subject_id}/correlation")
def subject_correlation(case_id: int, subject_id: str, db: Session = Depends(get_db)):
    return get_cross_correlation(db, case_id, subject_id)

class IntersectRequest(BaseModel):
    subject_ids: List[str]

@router.post("/case/{case_id}/intersect")
def subject_intersect(case_id: int, req: IntersectRequest, db: Session = Depends(get_db)):
    return get_common_contacts(db, case_id, req.subject_ids)

class QueryRequest(BaseModel):
    subject_id: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

@router.post("/case/{case_id}/query")
def subject_query(case_id: int, req: QueryRequest, db: Session = Depends(get_db)):
    return execute_query(db, case_id, req.dict())
