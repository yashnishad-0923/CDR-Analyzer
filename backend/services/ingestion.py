import pandas as pd
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import zoneinfo
from sqlalchemy.orm import Session

from models.database import CDRRecordDB, IPDRRecordDB, EvidenceLog
from models.schema import CDRRecord, IPDRRecord

def parse_csv(file_content: bytes) -> pd.DataFrame:
    import io
    df = pd.read_csv(io.BytesIO(file_content))
    return df

def get_normalized_time(dt: datetime, tz_name: Optional[str]) -> datetime:
    if not dt:
        return dt
    if not tz_name:
        tz_name = "UTC"
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(timezone.utc)
    except Exception:
        return dt

def normalize_cdr(df: pd.DataFrame, case_id: int, source_timezone: str = None) -> List[CDRRecordDB]:
    records = []
    col_mapping = {
        'sub_id': 'subject_id', 'subject': 'subject_id', 'imsi': 'subject_id',
        'type': 'event_type', 'event': 'event_type', 'call_type': 'event_type',
        'from': 'caller', 'calling_party': 'caller', 'suspect_number': 'caller', 
        'calling number': 'caller', 'calling_number': 'caller', 'a_number': 'caller', 'a number': 'caller',
        'to': 'callee', 'called_party': 'callee', 'called number': 'callee', 'called_number': 'callee',
        'b_number': 'callee', 'b number': 'callee',
        'start': 'start_time', 'time': 'start_time', 'timestamp': 'start_time', 'date_time': 'start_time', 'call_date': 'start_time',
        'dur': 'duration', 'dur_sec': 'duration', 'duration_sec': 'duration', 'duration (s)': 'duration',
        'cell': 'cell_id', 'tower': 'cell_id', 'cell_tower_id': 'cell_id', 'first_cell_id': 'cell_id'
    }
    
    df = df.rename(columns=lambda x: str(x).strip().lower())
    df = df.rename(columns=lambda x: col_mapping.get(x, x))
    
    if 'caller' not in df.columns or 'callee' not in df.columns:
        possible_cols = [c for c in df.columns if c not in ['start_time', 'duration', 'event_type', 'subject_id']]
        if 'caller' not in df.columns and len(possible_cols) > 0:
            df = df.rename(columns={possible_cols[0]: 'caller'})
        if 'callee' not in df.columns and len(possible_cols) > 1:
            df = df.rename(columns={possible_cols[1]: 'callee'})

    for _, row in df.iterrows():
        try:
            start_time_val = pd.to_datetime(row['start_time']) if 'start_time' in row and not pd.isna(row['start_time']) else datetime.now()
            start_time_val = start_time_val.to_pydatetime() if hasattr(start_time_val, 'to_pydatetime') else start_time_val
            
            norm_time = get_normalized_time(start_time_val, source_timezone)

            def get_str(col_name, default=''):
                val = row.get(col_name)
                if pd.isna(val): return default
                return str(val)

            record = CDRRecordDB(
                case_id=case_id,
                subject_id=get_str('subject_id', 'unknown'),
                event_type=get_str('event_type', 'voice'),
                caller=get_str('caller', 'Unknown_Caller'),
                callee=get_str('callee', 'Unknown_Callee'),
                start_time=start_time_val,
                duration=int(row.get('duration', 0)) if not pd.isna(row.get('duration')) else 0,
                cell_id=str(row.get('cell_id', '')) if 'cell_id' in row and not pd.isna(row.get('cell_id')) else None,
                imei=str(row.get('imei', '')) if 'imei' in row and not pd.isna(row.get('imei')) else None,
                imsi=str(row.get('imsi', '')) if 'imsi' in row and not pd.isna(row.get('imsi')) else None,
                operator=str(row.get('operator', '')) if 'operator' in row and not pd.isna(row.get('operator')) else None,
                source_timezone=source_timezone,
                normalized_time=norm_time
            )
            records.append(record)
        except Exception as e:
            print(f"Error normalizing row: {e}")
            
    return records

def ingest_cdr_file(db: Session, case_id: int, file_name: str, file_content: bytes, source_timezone: str = None) -> Dict[str, Any]:
    file_hash = hashlib.sha256(file_content).hexdigest()
    df = parse_csv(file_content)
    records = normalize_cdr(df, case_id, source_timezone)
    
    db.add_all(records)
    
    log = EvidenceLog(
        case_id=case_id,
        file_name=file_name,
        sha256_hash=file_hash,
        uploaded_by="Investigator",
        record_count=len(records),
        action="uploaded"
    )
    db.add(log)
    db.commit()
    
    return {"ingested_count": len(records), "status": "success", "hash": file_hash}

def get_all_cdrs(db: Session, case_id: int, start_date: str = None, end_date: str = None) -> List[CDRRecordDB]:
    query = db.query(CDRRecordDB).filter(CDRRecordDB.case_id == case_id)
    if start_date:
        try:
            sd = pd.to_datetime(start_date).to_pydatetime()
            query = query.filter(CDRRecordDB.normalized_time >= sd)
        except Exception:
            pass
    if end_date:
        try:
            ed = (pd.to_datetime(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)).to_pydatetime()
            query = query.filter(CDRRecordDB.normalized_time <= ed)
        except Exception:
            pass
            
    log = EvidenceLog(
        case_id=case_id,
        file_name="N/A",
        sha256_hash="N/A",
        uploaded_by="Investigator",
        record_count=query.count(),
        action="viewed"
    )
    db.add(log)
    db.commit()

    return query.all()
    
def normalize_ipdr(df: pd.DataFrame, case_id: int, source_timezone: str = None) -> List[IPDRRecordDB]:
    records = []
    col_mapping = {
        'sub_id': 'subject_id', 'subject': 'subject_id',
        'start': 'session_start', 'session_start': 'session_start',
        'end': 'session_end', 'session_end': 'session_end',
        'src_ip': 'source_ip', 'source_ip': 'source_ip',
        'src_port': 'source_port', 'source_port': 'source_port',
        'dst_ip': 'dest_ip', 'dest_ip': 'dest_ip', 'destination_ip': 'dest_ip',
        'dst_port': 'dest_port', 'dest_port': 'dest_port', 'destination_port': 'dest_port',
        'proto': 'protocol', 'protocol': 'protocol'
    }
    
    df = df.rename(columns=lambda x: str(x).strip().lower())
    df = df.rename(columns=lambda x: col_mapping.get(x, x))
    
    if 'source_ip' not in df.columns or 'dest_ip' not in df.columns:
        possible_cols = [c for c in df.columns if c not in ['session_start', 'session_end', 'subject_id', 'protocol']]
        if 'source_ip' not in df.columns and len(possible_cols) > 0:
            df = df.rename(columns={possible_cols[0]: 'source_ip'})
        if 'dest_ip' not in df.columns and len(possible_cols) > 1:
            df = df.rename(columns={possible_cols[1]: 'dest_ip'})

    for _, row in df.iterrows():
        try:
            start_time_val = pd.to_datetime(row['session_start']) if 'session_start' in row and not pd.isna(row['session_start']) else datetime.now()
            start_time_val = start_time_val.to_pydatetime() if hasattr(start_time_val, 'to_pydatetime') else start_time_val
            end_time_val = pd.to_datetime(row['session_end']) if 'session_end' in row and not pd.isna(row['session_end']) else datetime.now()
            end_time_val = end_time_val.to_pydatetime() if hasattr(end_time_val, 'to_pydatetime') else end_time_val
            
            norm_start = get_normalized_time(start_time_val, source_timezone)
            norm_end = get_normalized_time(end_time_val, source_timezone)

            def get_str(col_name, default=''):
                val = row.get(col_name)
                if pd.isna(val): return default
                return str(val)

            record = IPDRRecordDB(
                case_id=case_id,
                subject_id=get_str('subject_id', 'unknown'),
                session_start=start_time_val,
                session_end=end_time_val,
                source_ip=get_str('source_ip', '0.0.0.0'),
                source_port=int(row.get('source_port', 0)) if not pd.isna(row.get('source_port')) else 0,
                dest_ip=get_str('dest_ip', '0.0.0.0'),
                dest_port=int(row.get('dest_port', 0)) if not pd.isna(row.get('dest_port')) else 0,
                protocol=get_str('protocol', 'tcp'),
                apn=str(row.get('apn', '')) if 'apn' in row and not pd.isna(row.get('apn')) else None,
                source_timezone=source_timezone,
                normalized_session_start=norm_start,
                normalized_session_end=norm_end
            )
            records.append(record)
        except Exception as e:
            print(f"Error normalizing IPDR row: {e}")
            
    return records

def ingest_ipdr_file(db: Session, case_id: int, file_name: str, file_content: bytes, source_timezone: str = None) -> Dict[str, Any]:
    file_hash = hashlib.sha256(file_content).hexdigest()
    df = parse_csv(file_content)
    records = normalize_ipdr(df, case_id, source_timezone)
    
    db.add_all(records)
    
    log = EvidenceLog(
        case_id=case_id,
        file_name=file_name,
        sha256_hash=file_hash,
        uploaded_by="Investigator",
        record_count=len(records),
        action="uploaded"
    )
    db.add(log)
    db.commit()
    
    return {"ingested_count": len(records), "status": "success", "hash": file_hash}

def get_all_ipdrs(db: Session, case_id: int, start_date: str = None, end_date: str = None) -> List[IPDRRecordDB]:
    query = db.query(IPDRRecordDB).filter(IPDRRecordDB.case_id == case_id)
    if start_date:
        try:
            sd = pd.to_datetime(start_date).to_pydatetime()
            query = query.filter(IPDRRecordDB.normalized_session_start >= sd)
        except Exception:
            pass
    if end_date:
        try:
            ed = (pd.to_datetime(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)).to_pydatetime()
            query = query.filter(IPDRRecordDB.normalized_session_start <= ed)
        except Exception:
            pass
            
    log = EvidenceLog(
        case_id=case_id,
        file_name="N/A",
        sha256_hash="N/A",
        uploaded_by="Investigator",
        record_count=query.count(),
        action="viewed"
    )
    db.add(log)
    db.commit()

    return query.all()
