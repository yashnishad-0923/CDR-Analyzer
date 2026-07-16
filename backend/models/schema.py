from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime

class CDRRecord(BaseModel):
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

class IPDRRecord(BaseModel):
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

class CellTower(BaseModel):
    cell_id: str
    lat: float
    lon: float
    operator: Optional[str] = None
