# pyrefly: ignore [missing-import]
from fastapi import APIRouter, UploadFile, File, HTTPException, Response
from services.ingestion import ingest_cdr_file, get_all_cdrs, ingest_ipdr_file, get_all_ipdrs
from services.report import generate_pdf_report, generate_ipdr_pdf_report
from typing import Dict, Any

router = APIRouter()

@router.post("/upload/cdr")
async def upload_cdr(file: UploadFile = File(...)) -> Dict[str, Any]:
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are supported for now.")
    content = await file.read()
    return ingest_cdr_file(content)

@router.get("/cdrs")
async def list_cdrs(start_date: str = None, end_date: str = None):
    cdrs = get_all_cdrs(start_date, end_date)
    return {"cdrs": cdrs, "count": len(cdrs)}

@router.get("/report/pdf")
async def get_pdf_report(start_date: str = None, end_date: str = None):
    pdf_buffer = generate_pdf_report(start_date, end_date)
    return Response(
        content=pdf_buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=investigation_report.pdf"}
    )

@router.post("/upload/ipdr")
async def upload_ipdr(file: UploadFile = File(...)) -> Dict[str, Any]:
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are supported for now.")
    content = await file.read()
    return ingest_ipdr_file(content)

@router.get("/ipdrs")
async def list_ipdrs(start_date: str = None, end_date: str = None):
    ipdrs = get_all_ipdrs(start_date, end_date)
    return {"ipdrs": ipdrs, "count": len(ipdrs)}

@router.get("/report/ipdr/pdf")
async def get_ipdr_pdf_report(start_date: str = None, end_date: str = None):
    pdf_buffer = generate_ipdr_pdf_report(start_date, end_date)
    return Response(
        content=pdf_buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=ipdr_investigation_report.pdf"}
    )
