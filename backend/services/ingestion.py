import pandas as pd
from typing import List, Dict, Any
from models.schema import CDRRecord, IPDRRecord
from datetime import datetime

# Temporary in-memory storage for MVP
cdrs_db: List[CDRRecord] = []
ipdrs_db: List[IPDRRecord] = []

def parse_csv(file_content: bytes) -> pd.DataFrame:
    import io
    # Try reading as csv
    df = pd.read_csv(io.BytesIO(file_content))
    return df

def normalize_cdr(df: pd.DataFrame) -> List[CDRRecord]:
    """
    Naively normalizes a dataframe assuming it has some common column names.
    In a real system, this would use a configurable mapping per operator.
    """
    records = []
    # Map common column names to canonical names
    col_mapping = {
        'sub_id': 'subject_id', 'subject': 'subject_id',
        'type': 'event_type', 'event': 'event_type',
        'from': 'caller', 'calling_party': 'caller', 'suspect_number': 'caller',
        'to': 'callee', 'called_party': 'callee',
        'start': 'start_time', 'time': 'start_time', 'timestamp': 'start_time',
        'dur': 'duration', 'dur_sec': 'duration', 'duration_sec': 'duration',
        'cell': 'cell_id', 'tower': 'cell_id', 'cell_tower_id': 'cell_id'
    }
    
    # Rename columns to standard ones if they match
    df = df.rename(columns=lambda x: col_mapping.get(x.lower(), x.lower()))
    
    for _, row in df.iterrows():
        try:
            # Simple normalization
            start_time_val = pd.to_datetime(row['start_time']) if 'start_time' in row else datetime.now()
            
            record = CDRRecord(
                subject_id=str(row.get('subject_id', 'unknown')),
                event_type=str(row.get('event_type', 'voice')),
                caller=str(row.get('caller', '')),
                callee=str(row.get('callee', '')),
                start_time=start_time_val,
                duration=int(row.get('duration', 0)),
                cell_id=str(row.get('cell_id', '')) if 'cell_id' in row else None,
                imei=str(row.get('imei', '')) if 'imei' in row else None,
                imsi=str(row.get('imsi', '')) if 'imsi' in row else None,
                operator=str(row.get('operator', '')) if 'operator' in row else None
            )
            records.append(record)
        except Exception as e:
            # In a robust system, we would quarantine this row instead of skipping
            print(f"Error normalizing row: {e}")
            
    return records

def ingest_cdr_file(file_content: bytes) -> Dict[str, Any]:
    df = parse_csv(file_content)
    records = normalize_cdr(df)
    cdrs_db.extend(records)
    return {"ingested_count": len(records), "status": "success"}

def get_all_cdrs(start_date: str = None, end_date: str = None) -> List[CDRRecord]:
    filtered = cdrs_db
    if start_date:
        try:
            sd = pd.to_datetime(start_date)
            filtered = [c for c in filtered if c.start_time >= sd]
        except Exception:
            pass
    if end_date:
        try:
            # include the entire end date up to midnight
            ed = pd.to_datetime(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            filtered = [c for c in filtered if c.start_time <= ed]
        except Exception:
            pass
    return filtered

def normalize_ipdr(df: pd.DataFrame) -> List[IPDRRecord]:
    records = []
    # Map common column names to canonical names for IPDR
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
    
    df = df.rename(columns=lambda x: col_mapping.get(x.lower(), x.lower()))
    
    for _, row in df.iterrows():
        try:
            start_time_val = pd.to_datetime(row['session_start']) if 'session_start' in row else datetime.now()
            end_time_val = pd.to_datetime(row['session_end']) if 'session_end' in row else datetime.now()
            
            record = IPDRRecord(
                subject_id=str(row.get('subject_id', 'unknown')),
                session_start=start_time_val,
                session_end=end_time_val,
                source_ip=str(row.get('source_ip', '')),
                source_port=int(row.get('source_port', 0)),
                dest_ip=str(row.get('dest_ip', '')),
                dest_port=int(row.get('dest_port', 0)),
                protocol=str(row.get('protocol', 'tcp')),
                apn=str(row.get('apn', '')) if 'apn' in row else None
            )
            records.append(record)
        except Exception as e:
            print(f"Error normalizing IPDR row: {e}")
            
    return records

def ingest_ipdr_file(file_content: bytes) -> Dict[str, Any]:
    df = parse_csv(file_content)
    records = normalize_ipdr(df)
    ipdrs_db.extend(records)
    return {"ingested_count": len(records), "status": "success"}

def get_all_ipdrs(start_date: str = None, end_date: str = None) -> List[IPDRRecord]:
    filtered = ipdrs_db
    if start_date:
        try:
            sd = pd.to_datetime(start_date)
            filtered = [c for c in filtered if c.session_start >= sd]
        except Exception:
            pass
    if end_date:
        try:
            ed = pd.to_datetime(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            filtered = [c for c in filtered if c.session_start <= ed]
        except Exception:
            pass
    return filtered
