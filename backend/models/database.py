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
    evidence_logs = relationship("EvidenceLog", back_populates="case")
    anomaly_flags = relationship("AnomalyFlag", back_populates="case")

class CDRRecordDB(Base):
    __tablename__ = "cdrs"
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"))
    subject_id = Column(String, index=True)
    event_type = Column(String)          # voice / sms
    direction = Column(String, nullable=True)   # incoming / outgoing / sms-mo / sms-mt
    caller = Column(String, index=True)
    callee = Column(String, index=True)
    start_time = Column(DateTime, index=True)
    end_time = Column(DateTime, nullable=True)
    duration = Column(Integer)
    cell_id = Column(String, index=True, nullable=True)       # first cell id
    last_cell_id = Column(String, index=True, nullable=True)  # last cell id (handover)
    imei = Column(String, index=True, nullable=True)
    imsi = Column(String, index=True, nullable=True)
    operator = Column(String, nullable=True)
    roaming_center = Column(String, nullable=True)   # MSC / location signal
    source_timezone = Column(String, nullable=True)
    normalized_time = Column(DateTime, index=True)

    case = relationship("Case", back_populates="cdrs")



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

class CellTower(Base):
    __tablename__ = "cell_towers"
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"))
    cell_id = Column(String, index=True)
    latitude = Column(Float)
    longitude = Column(Float)
    operator = Column(String, nullable=True)
    address = Column(String, nullable=True)
    source = Column(String, nullable=True)      # "reference_csv" / "opencellid" / "roaming_centroid"
    confidence = Column(String, nullable=True)  # high / medium / low

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
    _run_lightweight_migrations()


def _run_lightweight_migrations():
    """Add columns introduced after the initial schema to pre-existing SQLite
    databases. SQLAlchemy's create_all never ALTERs existing tables, so we do
    it manually and idempotently."""
    from sqlalchemy import text
    new_columns = {
        "cdrs": {
            "direction": "VARCHAR",
            "end_time": "DATETIME",
            "last_cell_id": "VARCHAR",
            "roaming_center": "VARCHAR",
        },
        "cell_towers": {
            "source": "VARCHAR",
            "confidence": "VARCHAR",
        },
    }
    with engine.begin() as conn:
        for table, cols in new_columns.items():
            try:
                existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            except Exception:
                continue
            for col, col_type in cols.items():
                if col not in existing:
                    try:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                    except Exception:
                        pass

    _repair_float_artifacts()


def _repair_float_artifacts():
    """Older uploads stored IMSI/IMEI/cell IDs that pandas widened to float,
    leaving a trailing '.0' (e.g. '404459000000000.0'). Strip it in place so
    operator/IMEI lookups work without a re-upload. Idempotent."""
    from sqlalchemy import text
    id_cols = ["imsi", "imei", "cell_id", "last_cell_id", "caller", "callee"]
    try:
        with engine.begin() as conn:
            for col in id_cols:
                conn.execute(text(
                    f"UPDATE cdrs SET {col} = substr({col}, 1, length({col}) - 2) "
                    f"WHERE {col} LIKE '%.0' AND substr({col}, 1, length({col}) - 2) GLOB '[0-9]*'"
                ))
    except Exception:
        pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
