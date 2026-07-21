import pandas as pd
import hashlib
import io
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
import zoneinfo
from sqlalchemy.orm import Session

from models.database import CDRRecordDB, EvidenceLog, CellTower


def parse_csv(file_content: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(file_content))
    return df


def get_normalized_time(dt: datetime, tz_name: Optional[str]) -> datetime:
    """Convert a naive/aware datetime to UTC using the declared source timezone."""
    if not dt:
        return dt
    if not tz_name:
        tz_name = "UTC"
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return dt


def log_action(db: Session, case_id: int, action: str, file_name: str = "N/A",
               sha256_hash: str = "N/A", record_count: int = 0,
               uploaded_by: str = "Investigator", commit: bool = True):
    """Central chain-of-custody logger. Only meaningful evidentiary actions
    (upload / export / report generation) are recorded — routine dashboard
    views are intentionally NOT logged to keep the custody trail legible."""
    log = EvidenceLog(
        case_id=case_id,
        file_name=file_name,
        sha256_hash=sha256_hash,
        uploaded_by=uploaded_by,
        record_count=record_count,
        action=action,
    )
    db.add(log)
    if commit:
        db.commit()


CDR_COL_MAPPING = {
    # subject
    'sub_id': 'subject_id', 'subject': 'subject_id', 'msisdn': 'subject_id',
    # event / call type
    'type': 'call_type', 'event': 'call_type', 'call_type': 'call_type', 'call type': 'call_type',
    'usage_type': 'call_type', 'usage type': 'call_type',
    # caller
    'from': 'caller', 'calling_party': 'caller', 'suspect_number': 'caller',
    'calling number': 'caller', 'calling_number': 'caller', 'a_number': 'caller', 'a number': 'caller',
    'calling party telephone number': 'caller', 'calling party number': 'caller',
    'calling party telephone no': 'caller', 'calling msisdn': 'caller',
    # callee
    'to': 'callee', 'called_party': 'callee', 'called number': 'callee', 'called_number': 'callee',
    'b_number': 'callee', 'b number': 'callee',
    'called party phone number': 'callee', 'called party number': 'callee',
    'called party telephone number': 'callee', 'called msisdn': 'callee',
    # timestamps
    'start': 'start_time', 'time': 'call_time', 'timestamp': 'start_time',
    'date_time': 'start_time', 'call_date': 'call_date', 'call date': 'call_date',
    'date': 'call_date', 'call_time': 'call_time', 'call time': 'call_time',
    'call termination time': 'end_time', 'termination time': 'end_time', 'end_time': 'end_time',
    'call end time': 'end_time',
    # duration
    'dur': 'duration', 'dur_sec': 'duration', 'duration_sec': 'duration', 'duration (s)': 'duration',
    'call duration (seconds)': 'duration', 'call duration': 'duration', 'duration (seconds)': 'duration',
    'call_duration': 'duration',
    # cell / tower
    'cell': 'cell_id', 'tower': 'cell_id', 'cell_tower_id': 'cell_id', 'first_cell_id': 'cell_id',
    'first cell id': 'cell_id', 'cell id': 'cell_id', 'first cellid': 'cell_id',
    'last_cell_id': 'last_cell_id', 'last cell id': 'last_cell_id', 'last cellid': 'last_cell_id',
    # identifiers
    'imei': 'imei', 'imsi': 'imsi',
    # location / roaming
    'roaming center': 'roaming_center', 'roaming_center': 'roaming_center', 'msc': 'roaming_center',
    'location': 'roaming_center', 'roaming centre': 'roaming_center',
    # sms centre (kept for completeness, unused downstream)
    'sms center number': 'sms_center', 'sms centre number': 'sms_center',
}


# Call Type -> (event_type, direction)
CALL_TYPE_MAP = {
    'incoming': ('voice', 'incoming'), 'in': ('voice', 'incoming'),
    'mtc': ('voice', 'incoming'), 'moc': ('voice', 'outgoing'),
    'outgoing': ('voice', 'outgoing'), 'out': ('voice', 'outgoing'),
    'voice': ('voice', None), 'call': ('voice', None),
    'sms': ('sms', None), 'sms-mo': ('sms', 'outgoing'), 'sms-mt': ('sms', 'incoming'),
    'sms_mo': ('sms', 'outgoing'), 'sms_mt': ('sms', 'incoming'),
    'smsmo': ('sms', 'outgoing'), 'smsmt': ('sms', 'incoming'),
}


def _clean(val, default=''):
    """Normalize a raw cell value to a trimmed string, treating NaN / N/A / blanks as empty."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    # Pandas widens integer-only columns (phone numbers, IMEI, IMSI, cell IDs) to
    # float when any cell is blank, turning 404459000000000 into "404459000000000.0".
    # Recover the original integer string so operator/IMEI lookups don't break.
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    s = str(val).strip()
    if s.lower() in ('', 'nan', 'n/a', 'na', 'none', 'null', '-'):
        return default
    # Same artifact when the value arrives as a string like "404459000000000.0".
    if s.endswith('.0') and s[:-2].isdigit():
        s = s[:-2]
    return s


def _combine_datetime(date_val, time_val, single_val):
    """Build a datetime from either a combined column or separate date + time columns."""
    date_s = _clean(date_val)
    time_s = _clean(time_val)
    single_s = _clean(single_val)

    if date_s and time_s:
        raw = f"{date_s} {time_s}"
    elif single_s:
        raw = single_s
    elif date_s:
        raw = date_s
    else:
        return None
    try:
        return pd.to_datetime(raw, dayfirst=False).to_pydatetime()
    except Exception:
        try:
            return pd.to_datetime(raw, dayfirst=True).to_pydatetime()
        except Exception:
            return None


def normalize_cdr(df: pd.DataFrame, case_id: int, source_timezone: str = None) -> Tuple[List[CDRRecordDB], List[Dict]]:
    """Normalize heterogeneous CDR exports into the canonical schema.
    Supports both the simple mock format (single start_time column) and real
    operator exports with separate Call Date + Call Time, First/Last Cell ID,
    Call Type (Incoming/Outgoing/SMS-MO/SMS-MT) and a Roaming Center column.
    Returns (valid_records, quarantined_rows). Rows without a parseable
    timestamp are quarantined, never silently back-filled."""
    records, quarantined = [], []

    df = df.rename(columns=lambda x: str(x).strip().lower())
    df = df.rename(columns=lambda x: CDR_COL_MAPPING.get(x, x))

    if 'caller' not in df.columns or 'callee' not in df.columns:
        reserved = {'start_time', 'call_date', 'call_time', 'end_time', 'duration',
                    'call_type', 'event_type', 'subject_id', 'cell_id', 'last_cell_id',
                    'imei', 'imsi', 'operator', 'roaming_center', 'sms_center'}
        possible_cols = [c for c in df.columns if c not in reserved]
        if 'caller' not in df.columns and len(possible_cols) > 0:
            df = df.rename(columns={possible_cols[0]: 'caller'})
        if 'callee' not in df.columns and len(possible_cols) > 1:
            df = df.rename(columns={possible_cols[1]: 'callee'})

    for idx, row in df.iterrows():
        try:
            start_time_val = _combine_datetime(
                row.get('call_date'), row.get('call_time'), row.get('start_time'))
            if start_time_val is None:
                quarantined.append({"row": int(idx) + 2, "reason": "Missing or unparseable timestamp"})
                continue

            end_time_val = _combine_datetime(
                row.get('call_date'), row.get('end_time'), None)

            norm_time = get_normalized_time(start_time_val, source_timezone)

            caller = _clean(row.get('caller'))
            callee = _clean(row.get('callee'))
            if not caller or not callee:
                quarantined.append({"row": int(idx) + 2, "reason": "Missing caller/callee"})
                continue

            raw_type = _clean(row.get('call_type')) or _clean(row.get('event_type'))
            event_type, direction = CALL_TYPE_MAP.get(raw_type.lower(), ('voice', None))
            if not raw_type:
                event_type = 'voice'

            dur_raw = row.get('duration')
            try:
                duration = int(float(dur_raw)) if not pd.isna(dur_raw) else 0
            except Exception:
                duration = 0

            record = CDRRecordDB(
                case_id=case_id,
                subject_id=_clean(row.get('subject_id')) or caller,
                event_type=event_type,
                direction=direction,
                caller=caller,
                callee=callee,
                start_time=start_time_val.replace(tzinfo=None) if start_time_val.tzinfo else start_time_val,
                end_time=(end_time_val.replace(tzinfo=None) if end_time_val and end_time_val.tzinfo else end_time_val),
                duration=duration,
                cell_id=_clean(row.get('cell_id')) or None,
                last_cell_id=_clean(row.get('last_cell_id')) or None,
                imei=_clean(row.get('imei')) or None,
                imsi=_clean(row.get('imsi')) or None,
                operator=_clean(row.get('operator')) or None,
                roaming_center=_clean(row.get('roaming_center')) or None,
                source_timezone=source_timezone,
                normalized_time=norm_time,
            )
            records.append(record)
        except Exception as e:
            quarantined.append({"row": int(idx) + 2, "reason": str(e)})

    return records, quarantined


def ingest_cdr_file(db: Session, case_id: int, file_name: str, file_content: bytes,
                    source_timezone: str = None) -> Dict[str, Any]:
    file_hash = hashlib.sha256(file_content).hexdigest()
    df = parse_csv(file_content)
    records, quarantined = normalize_cdr(df, case_id, source_timezone)

    db.add_all(records)
    log_action(db, case_id, "uploaded", file_name, file_hash, len(records), commit=False)
    db.commit()

    return {
        "ingested_count": len(records),
        "quarantined_count": len(quarantined),
        "quarantined": quarantined[:20],
        "status": "success",
        "hash": file_hash,
    }


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
    return query.order_by(CDRRecordDB.normalized_time.asc()).all()




def ingest_tower_file(db: Session, case_id: int, file_name: str, file_content: bytes) -> Dict[str, Any]:
    """Ingest a cell-tower reference CSV: cell_id, latitude, longitude[, operator, address]."""
    file_hash = hashlib.sha256(file_content).hexdigest()
    df = parse_csv(file_content)
    df = df.rename(columns=lambda x: str(x).strip().lower())
    df = df.rename(columns={'lat': 'latitude', 'lon': 'longitude', 'lng': 'longitude', 'long': 'longitude',
                            'tower_id': 'cell_id', 'tower': 'cell_id'})

    towers, quarantined = [], []
    for idx, row in df.iterrows():
        try:
            cell_id = str(row['cell_id']).strip()
            lat = float(row['latitude'])
            lon = float(row['longitude'])
            towers.append(CellTower(
                case_id=case_id, cell_id=cell_id, latitude=lat, longitude=lon,
                operator=str(row.get('operator', '')) if not pd.isna(row.get('operator')) else None,
                address=str(row.get('address', '')) if not pd.isna(row.get('address')) else None,
            ))
        except Exception as e:
            quarantined.append({"row": int(idx) + 2, "reason": str(e)})

    # Replace existing reference data for this case (idempotent re-upload)
    db.query(CellTower).filter(CellTower.case_id == case_id).delete()
    db.add_all(towers)
    log_action(db, case_id, "uploaded", file_name, file_hash, len(towers), commit=False)
    db.commit()

    return {"ingested_count": len(towers), "quarantined_count": len(quarantined),
            "status": "success", "hash": file_hash}
