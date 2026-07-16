from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime

class CaseBase(BaseModel):
    case_name: str
    case_number: str
    notes: Optional[str] = None

class CaseCreate(CaseBase):
    created_by: str

class CaseResponse(CaseBase):
    id: int
    created_at: datetime
    status: str
    created_by: str
    
    class Config:
        from_attributes = True

class CDRRecord(BaseModel):
    id: Optional[int] = None
    case_id: Optional[int] = None
    subject_id: str
    event_type: str = Field(description="voice or SMS")
    caller: str
    callee: str
    start_time: datetime
    duration: int = Field(description="duration in seconds")
    cell_id: Optional[str] = None
    imei: Optional[str] = None
    imsi: Optional[str] = None
    operator: Optional[str] = None
    source_timezone: Optional[str] = None
    normalized_time: Optional[datetime] = None

    class Config:
        from_attributes = True

class IPDRRecord(BaseModel):
    id: Optional[int] = None
    case_id: Optional[int] = None
    subject_id: str
    session_start: datetime
    session_end: datetime
    source_ip: str
    source_port: int
    dest_ip: str
    dest_port: int
    protocol: str
    apn: Optional[str] = None
    data_volume_up: Optional[int] = 0
    data_volume_down: Optional[int] = 0
    nat_translation_ref: Optional[str] = None
    source_timezone: Optional[str] = None
    normalized_session_start: Optional[datetime] = None
    normalized_session_end: Optional[datetime] = None

    class Config:
        from_attributes = True

class EvidenceLogResponse(BaseModel):
    id: int
    case_id: int
    file_name: str
    sha256_hash: str
    uploaded_by: str
    upload_timestamp: datetime
    record_count: int
    action: str

    class Config:
        orm_mode = True

class AnomalyResponse(BaseModel):
    id: int
    case_id: int
    subject_id: str
    flag_type: str
    start_time: datetime
    end_time: datetime
    description: str
    severity: str
    confidence: Optional[str] = None

    class Config:
        orm_mode = True

class CellTower(BaseModel):
    cell_id: str
    lat: float
    lon: float
    operator: Optional[str] = None
