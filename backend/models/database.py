from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
import os

# Create SQLite database in the backend directory
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'investigation.db')
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Case(Base):
    __tablename__ = "cases"
    id = Column(Integer, primary_key=True, index=True)
    case_name = Column(String, index=True)
    case_number = Column(String, index=True)
    created_by = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="open")
    notes = Column(String)

    cdrs = relationship("CDRRecordDB", back_populates="case")
    ipdrs = relationship("IPDRRecordDB", back_populates="case")
    evidence_logs = relationship("EvidenceLog", back_populates="case")
    anomaly_flags = relationship("AnomalyFlag", back_populates="case")

class CDRRecordDB(Base):
    __tablename__ = "cdrs"
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"))
    subject_id = Column(String, index=True)
    event_type = Column(String)
    caller = Column(String, index=True)
    callee = Column(String, index=True)
    start_time = Column(DateTime, index=True)
    duration = Column(Integer)
    cell_id = Column(String, index=True, nullable=True)
    imei = Column(String, index=True, nullable=True)
    imsi = Column(String, index=True, nullable=True)
    operator = Column(String, nullable=True)
    source_timezone = Column(String, nullable=True)
    normalized_time = Column(DateTime, index=True)

    case = relationship("Case", back_populates="cdrs")

class IPDRRecordDB(Base):
    __tablename__ = "ipdrs"
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"))
    subject_id = Column(String, index=True)
    session_start = Column(DateTime, index=True)
    session_end = Column(DateTime, index=True)
    source_ip = Column(String, index=True)
    source_port = Column(Integer)
    dest_ip = Column(String, index=True)
    dest_port = Column(Integer)
    protocol = Column(String)
    apn = Column(String, nullable=True)
    data_volume_up = Column(Integer, default=0)
    data_volume_down = Column(Integer, default=0)
    nat_translation_ref = Column(String, nullable=True)
    source_timezone = Column(String, nullable=True)
    normalized_session_start = Column(DateTime, index=True)
    normalized_session_end = Column(DateTime, index=True)

    case = relationship("Case", back_populates="ipdrs")

class EvidenceLog(Base):
    __tablename__ = "evidence_logs"
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"))
    file_name = Column(String)
    sha256_hash = Column(String)
    uploaded_by = Column(String)
    upload_timestamp = Column(DateTime, default=datetime.utcnow)
    record_count = Column(Integer)
    action = Column(String)  # "uploaded", "viewed", "exported", "report_generated"

    case = relationship("Case", back_populates="evidence_logs")

class AnomalyFlag(Base):
    __tablename__ = "anomaly_flags"
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"))
    subject_id = Column(String, index=True)
    flag_type = Column(String)  # enum: gap/imei_swap/burst_silence
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    description = Column(String)
    severity = Column(String)  # low/medium/high
    confidence = Column(String, nullable=True) # high/medium/low

    case = relationship("Case", back_populates="anomaly_flags")

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
