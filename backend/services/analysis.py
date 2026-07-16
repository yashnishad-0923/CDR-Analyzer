import pandas as pd
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from datetime import timedelta
import numpy as np

from models.database import CDRRecordDB, IPDRRecordDB, AnomalyFlag, EvidenceLog

def get_custody_log(db: Session, case_id: int):
    return db.query(EvidenceLog).filter(EvidenceLog.case_id == case_id).order_by(EvidenceLog.upload_timestamp.asc()).all()

def detect_anomalies(db: Session, case_id: int):
    cdrs = db.query(CDRRecordDB).filter(CDRRecordDB.case_id == case_id).all()
    if not cdrs:
        return
        
    df = pd.DataFrame([{
        'subject_id': c.subject_id,
        'start_time': c.normalized_time,
        'imei': c.imei,
        'imsi': c.imsi,
        'duration': c.duration
    } for c in cdrs if c.normalized_time])
    
    if df.empty:
        return
        
    df = df.sort_values(by=['subject_id', 'start_time'])
    
    anomalies = []
    
    for subject_id, group in df.groupby('subject_id'):
        group = group.reset_index(drop=True)
        if len(group) < 2:
            continue
            
        # 1. Log Gap detection (e.g. > 6 hours)
        group['time_diff'] = group['start_time'].diff()
        gaps = group[group['time_diff'] > pd.Timedelta(hours=6)]
        for _, row in gaps.iterrows():
            anomalies.append(AnomalyFlag(
                case_id=case_id,
                subject_id=subject_id,
                flag_type="gap",
                start_time=row['start_time'] - row['time_diff'],
                end_time=row['start_time'],
                description=f"Log gap of {row['time_diff']} detected",
                severity="medium",
                confidence="medium"
            ))
            
        # 2. IMEI/IMSI churn
        unique_imeis = group['imei'].dropna().unique()
        unique_imsis = group['imsi'].dropna().unique()
        if len(unique_imeis) > 1 and len(unique_imsis) == 1:
            anomalies.append(AnomalyFlag(
                case_id=case_id, subject_id=subject_id, flag_type="imei_swap",
                start_time=group['start_time'].min(), end_time=group['start_time'].max(),
                description=f"SIM {unique_imsis[0]} used in {len(unique_imeis)} different devices",
                severity="high", confidence="high"
            ))
            
    db.query(AnomalyFlag).filter(AnomalyFlag.case_id == case_id).delete()
    db.add_all(anomalies)
    db.commit()

def get_anomalies(db: Session, case_id: int):
    detect_anomalies(db, case_id)
    return db.query(AnomalyFlag).filter(AnomalyFlag.case_id == case_id).all()

def get_behavior_profile(db: Session, case_id: int, subject_id: str):
    cdrs = db.query(CDRRecordDB).filter(CDRRecordDB.case_id == case_id, CDRRecordDB.subject_id == subject_id).all()
    
    if not cdrs:
        return {"error": "No data found for subject"}
        
    df = pd.DataFrame([{
        'start_time': c.normalized_time if c.normalized_time else c.start_time,
        'duration': c.duration
    } for c in cdrs])
    
    df['hour'] = df['start_time'].dt.hour
    df['day_of_week'] = df['start_time'].dt.dayofweek
    
    hourly_counts = df['hour'].value_counts().reindex(range(24), fill_value=0).to_dict()
    dow_counts = df['day_of_week'].value_counts().reindex(range(7), fill_value=0).to_dict()
    
    odd_hours_count = df[(df['hour'] >= 23) | (df['hour'] <= 5)].shape[0]
    odd_hours_pct = (odd_hours_count / len(df)) * 100 if len(df) > 0 else 0
    
    return {
        "hourly_distribution": hourly_counts,
        "day_of_week_distribution": dow_counts,
        "odd_hours_percentage": round(odd_hours_pct, 2),
        "avg_duration": round(df['duration'].mean(), 2),
        "total_calls": len(df)
    }

def get_cross_correlation(db: Session, case_id: int, subject_id: str):
    cdrs = db.query(CDRRecordDB).filter(CDRRecordDB.case_id == case_id, CDRRecordDB.subject_id == subject_id).all()
    ipdrs = db.query(IPDRRecordDB).filter(IPDRRecordDB.case_id == case_id, IPDRRecordDB.subject_id == subject_id).all()
    
    if not cdrs or not ipdrs:
        return {"overlap_percentage": 0, "overlaps": [], "confidence": "low"}
        
    cdr_df = pd.DataFrame([{'id': c.id, 'time': c.normalized_time} for c in cdrs if c.normalized_time])
    ipdr_df = pd.DataFrame([{'id': i.id, 'start': i.normalized_session_start, 'end': i.normalized_session_end} for i in ipdrs if i.normalized_session_start and i.normalized_session_end])
    
    if cdr_df.empty or ipdr_df.empty:
        return {"overlap_percentage": 0, "overlaps": [], "confidence": "low"}
    
    overlaps = []
    for _, cdr in cdr_df.iterrows():
        active = ipdr_df[(ipdr_df['start'] <= cdr['time']) & (ipdr_df['end'] >= cdr['time'])]
        if not active.empty:
            overlaps.append({
                "cdr_id": cdr['id'],
                "ipdr_ids": active['id'].tolist(),
                "time": cdr['time'].isoformat()
            })
            
    pct = (len(overlaps) / len(cdr_df)) * 100 if len(cdr_df) > 0 else 0
    
    return {
        "overlap_percentage": round(pct, 2),
        "overlaps": overlaps,
        "confidence": "medium"
    }

def get_common_contacts(db: Session, case_id: int, subject_ids: List[str]):
    sets = []
    for sid in subject_ids:
        callers = db.query(CDRRecordDB.caller).filter(CDRRecordDB.case_id == case_id, CDRRecordDB.callee == sid).all()
        callees = db.query(CDRRecordDB.callee).filter(CDRRecordDB.case_id == case_id, CDRRecordDB.caller == sid).all()
        contacts = set([c[0] for c in callers] + [c[0] for c in callees])
        if sid in contacts:
            contacts.remove(sid)
        sets.append(contacts)
        
    if not sets:
        return {"common_contacts": [], "confidence": "high"}
        
    common = set.intersection(*sets)
    return {"common_contacts": list(common), "confidence": "high"}

def execute_query(db: Session, case_id: int, filters: dict):
    query = db.query(CDRRecordDB).filter(CDRRecordDB.case_id == case_id)
    
    if filters.get("subject_id"):
        sid = filters["subject_id"]
        query = query.filter((CDRRecordDB.caller == sid) | (CDRRecordDB.callee == sid))
        
    if filters.get("start_date"):
        try:
            query = query.filter(CDRRecordDB.normalized_time >= pd.to_datetime(filters["start_date"]).to_pydatetime())
        except Exception: pass
        
    if filters.get("end_date"):
        try:
            query = query.filter(CDRRecordDB.normalized_time <= pd.to_datetime(filters["end_date"]).to_pydatetime())
        except Exception: pass
        
    return query.all()
