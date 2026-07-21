import pandas as pd
import numpy as np
import networkx as nx
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from datetime import timedelta

from models.database import CDRRecordDB, AnomalyFlag, EvidenceLog, CellTower, Case


def get_custody_log(db: Session, case_id: int):
    return (db.query(EvidenceLog)
            .filter(EvidenceLog.case_id == case_id)
            .order_by(EvidenceLog.upload_timestamp.asc())
            .all())


# ---------------------------------------------------------------------------
# Anomaly detection (explainable, rule-based — see FR9 / Feature 3)
# ---------------------------------------------------------------------------

def detect_anomalies(db: Session, case_id: int):
    cdrs = db.query(CDRRecordDB).filter(CDRRecordDB.case_id == case_id).all()
    if not cdrs:
        return

    df = pd.DataFrame([{
        'subject_id': c.subject_id,
        'start_time': c.normalized_time,
        'imei': c.imei,
        'imsi': c.imsi,
        'duration': c.duration,
    } for c in cdrs if c.normalized_time])

    if df.empty:
        return

    df = df.sort_values(by=['subject_id', 'start_time'])
    anomalies = []

    for subject_id, group in df.groupby('subject_id'):
        group = group.reset_index(drop=True)
        if len(group) < 2:
            continue

        # 1. Log-gap detection (> 6 hours between consecutive events)
        group['time_diff'] = group['start_time'].diff()
        gaps = group[group['time_diff'] > pd.Timedelta(hours=6)]
        for _, row in gaps.iterrows():
            hours = round(row['time_diff'].total_seconds() / 3600, 1)
            anomalies.append(AnomalyFlag(
                case_id=case_id,
                subject_id=subject_id,
                flag_type="gap",
                start_time=row['start_time'] - row['time_diff'],
                end_time=row['start_time'],
                description=f"Communication silence of {hours} hours detected between consecutive events",
                severity="medium",
                confidence="medium",
            ))

        # 2. IMEI/IMSI churn (device or SIM swap)
        unique_imeis = group['imei'].dropna().unique()
        unique_imsis = group['imsi'].dropna().unique()
        if len(unique_imeis) > 1 and len(unique_imsis) == 1:
            anomalies.append(AnomalyFlag(
                case_id=case_id, subject_id=subject_id, flag_type="imei_swap",
                start_time=group['start_time'].min(), end_time=group['start_time'].max(),
                description=f"SIM (IMSI {unique_imsis[0]}) observed in {len(unique_imeis)} different handsets (IMEI change) — possible device swap",
                severity="high", confidence="high",
            ))
        if len(unique_imsis) > 1 and len(unique_imeis) == 1:
            anomalies.append(AnomalyFlag(
                case_id=case_id, subject_id=subject_id, flag_type="sim_swap",
                start_time=group['start_time'].min(), end_time=group['start_time'].max(),
                description=f"Handset (IMEI {unique_imeis[0]}) used with {len(unique_imsis)} different SIMs (IMSI change) — possible SIM swap / burner pattern",
                severity="high", confidence="high",
            ))

        # 3. Burst-then-silence: unusually high-activity day followed by no activity
        daily = group.set_index('start_time').resample('D').size()
        if len(daily) >= 3 and daily.std() > 0:
            z = (daily - daily.mean()) / daily.std()
            for day, score in z.items():
                if score > 2 and daily.get(day + pd.Timedelta(days=1), 0) == 0:
                    anomalies.append(AnomalyFlag(
                        case_id=case_id, subject_id=subject_id, flag_type="burst_silence",
                        start_time=day, end_time=day + pd.Timedelta(days=1),
                        description=(f"Activity burst of {int(daily[day])} events on {day.date()} "
                                     f"(z-score {round(float(score), 1)}) followed by complete silence — "
                                     f"pattern consistent with 'going dark'"),
                        severity="high", confidence="medium",
                    ))

        # 4. Odd-hour concentration (>50% of activity between 23:00–05:00)
        hours = group['start_time'].dt.hour
        odd = ((hours >= 23) | (hours <= 5)).sum()
        if len(group) >= 5 and odd / len(group) > 0.5:
            anomalies.append(AnomalyFlag(
                case_id=case_id, subject_id=subject_id, flag_type="odd_hours",
                start_time=group['start_time'].min(), end_time=group['start_time'].max(),
                description=f"{round(100 * odd / len(group))}% of activity occurs between 23:00–05:00 UTC ({odd} of {len(group)} events)",
                severity="medium", confidence="medium",
            ))

    db.query(AnomalyFlag).filter(AnomalyFlag.case_id == case_id).delete()
    db.add_all(anomalies)
    db.commit()


def get_anomalies(db: Session, case_id: int):
    detect_anomalies(db, case_id)
    flags = db.query(AnomalyFlag).filter(AnomalyFlag.case_id == case_id).all()
    order = {"high": 0, "medium": 1, "low": 2}
    flags.sort(key=lambda f: order.get(f.severity, 3))
    return flags


# ---------------------------------------------------------------------------
# Behavior profile (Feature 5)
# ---------------------------------------------------------------------------

def get_behavior_profile(db: Session, case_id: int, subject_id: str):
    cdrs = db.query(CDRRecordDB).filter(
        CDRRecordDB.case_id == case_id,
        ((CDRRecordDB.subject_id == subject_id) |
         (CDRRecordDB.caller == subject_id) |
         (CDRRecordDB.callee == subject_id))
    ).all()

    if not cdrs:
        return {"error": f"No CDR data found for subject '{subject_id}' in this case"}

    df = pd.DataFrame([{
        'start_time': c.normalized_time if c.normalized_time else c.start_time,
        'duration': c.duration,
    } for c in cdrs])

    df['hour'] = df['start_time'].dt.hour
    df['day_of_week'] = df['start_time'].dt.dayofweek

    hourly_counts = df['hour'].value_counts().reindex(range(24), fill_value=0).astype(int).to_dict()
    dow_counts = df['day_of_week'].value_counts().reindex(range(7), fill_value=0).astype(int).to_dict()

    odd_hours_count = int(df[(df['hour'] >= 23) | (df['hour'] <= 5)].shape[0])
    odd_hours_pct = (odd_hours_count / len(df)) * 100 if len(df) > 0 else 0

    # Burst days via z-score on daily counts
    daily = df.set_index('start_time').resample('D').size()
    burst_days = []
    if len(daily) >= 3 and daily.std() > 0:
        z = (daily - daily.mean()) / daily.std()
        burst_days = [{"date": str(d.date()), "count": int(daily[d]), "z_score": round(float(s), 2)}
                      for d, s in z.items() if s > 2]

    return {
        "subject_id": subject_id,
        "hourly_distribution": hourly_counts,
        "day_of_week_distribution": dow_counts,
        "odd_hours_percentage": round(odd_hours_pct, 2),
        "avg_duration": round(float(df['duration'].mean()), 2),
        "median_duration": round(float(df['duration'].median()), 2),
        "std_duration": round(float(df['duration'].std()), 2) if len(df) > 1 else 0,
        "total_calls": int(len(df)),
        "first_seen": df['start_time'].min().isoformat(),
        "last_seen": df['start_time'].max().isoformat(),
        "burst_days": burst_days,
    }





# ---------------------------------------------------------------------------
# Structured query (Feature 8) — returns serializable dicts, not ORM objects
# ---------------------------------------------------------------------------

def execute_query(db: Session, case_id: int, filters: dict):
    query = db.query(CDRRecordDB).filter(CDRRecordDB.case_id == case_id)

    if filters.get("subject_id"):
        sid = filters["subject_id"]
        query = query.filter((CDRRecordDB.caller == sid) | (CDRRecordDB.callee == sid))

    if filters.get("start_date"):
        try:
            query = query.filter(CDRRecordDB.normalized_time >= pd.to_datetime(filters["start_date"]).to_pydatetime())
        except Exception:
            pass

    if filters.get("end_date"):
        try:
            query = query.filter(CDRRecordDB.normalized_time <= pd.to_datetime(filters["end_date"]).to_pydatetime())
        except Exception:
            pass

    rows = query.order_by(CDRRecordDB.normalized_time.asc()).all()
    return [{
        "id": r.id,
        "caller": r.caller,
        "callee": r.callee,
        "event_type": r.event_type,
        "normalized_time": r.normalized_time.isoformat() if r.normalized_time else None,
        "duration": r.duration,
        "cell_id": r.cell_id,
    } for r in rows]


# ---------------------------------------------------------------------------
# NEW: Unified activity timeline (CDR chronologically)
# ---------------------------------------------------------------------------

def get_unified_timeline(db: Session, case_id: int, subject_id: Optional[str] = None, limit: int = 500):
    cdr_q = db.query(CDRRecordDB).filter(CDRRecordDB.case_id == case_id)
    if subject_id:
        cdr_q = cdr_q.filter((CDRRecordDB.subject_id == subject_id) |
                             (CDRRecordDB.caller == subject_id) |
                             (CDRRecordDB.callee == subject_id))

    events = []
    for c in cdr_q.all():
        t = c.normalized_time or c.start_time
        if not t:
            continue
        events.append({
            "time": t.isoformat(),
            "source": "CDR",
            "type": c.event_type or "voice",
            "summary": f"{c.caller} → {c.callee}" + (f" ({c.duration}s)" if c.duration else ""),
            "subject_id": c.subject_id,
            "detail": {"cell_id": c.cell_id, "imei": c.imei, "duration": c.duration},
        })

    events.sort(key=lambda e: e["time"])
    return {"count": len(events), "events": events[:limit]}


# ---------------------------------------------------------------------------
# NEW: Graph metrics — key actor ranking via NetworkX (FR5)
# ---------------------------------------------------------------------------

def get_graph_metrics(db: Session, case_id: int, data_type: str = "cdr", top_n: int = 15):
    G = nx.Graph()

    rows = db.query(CDRRecordDB).filter(CDRRecordDB.case_id == case_id).all()
    for r in rows:
        if r.caller and r.callee:
            if G.has_edge(r.caller, r.callee):
                G[r.caller][r.callee]['weight'] += 1
            else:
                G.add_edge(r.caller, r.callee, weight=1)

    if G.number_of_nodes() == 0:
        return {"nodes": 0, "edges": 0, "key_actors": [], "communities": []}

    degree = nx.degree_centrality(G)
    betweenness = nx.betweenness_centrality(G, weight='weight')
    try:
        eigen = nx.eigenvector_centrality(G, max_iter=500, weight='weight')
    except Exception:
        eigen = {n: 0 for n in G.nodes()}

    # Composite ranking
    ranked = sorted(G.nodes(), key=lambda n: (degree[n] + betweenness[n] + eigen[n]), reverse=True)

    key_actors = [{
        "entity": n,
        "degree": G.degree(n),
        "degree_centrality": round(degree[n], 4),
        "betweenness": round(betweenness[n], 4),
        "eigenvector": round(eigen[n], 4),
        "total_interactions": int(sum(d['weight'] for _, _, d in G.edges(n, data=True))),
    } for n in ranked[:top_n]]

    # Community detection (greedy modularity)
    communities = []
    try:
        from networkx.algorithms.community import greedy_modularity_communities
        comms = greedy_modularity_communities(G, weight='weight')
        communities = [sorted(c) for c in comms if len(c) > 1][:10]
    except Exception:
        pass

    return {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "density": round(nx.density(G), 4),
        "key_actors": key_actors,
        "communities": communities,
        "confidence": "medium",
    }


# ---------------------------------------------------------------------------
# NEW: Cell-tower movement reconstruction (Feature 7 / FR8)
# ---------------------------------------------------------------------------

def get_movement(db: Session, case_id: int, subject_id: str, auto_geolocate: bool = True):
    cdrs = db.query(CDRRecordDB).filter(
        CDRRecordDB.case_id == case_id,
        ((CDRRecordDB.subject_id == subject_id) |
         (CDRRecordDB.caller == subject_id) |
         (CDRRecordDB.callee == subject_id)),
        CDRRecordDB.cell_id.isnot(None),
    ).order_by(CDRRecordDB.normalized_time.asc()).all()

    if not cdrs:
        return {"dwell_periods": [], "has_coordinates": False,
                "message": f"No cell-tower data found for subject '{subject_id}'"}

    # Ensure coordinates exist for this case's cells. To keep the movement view
    # snappy this auto-pass uses only the instant roaming-centre fallback; the
    # (potentially slow) OpenCellID network lookups are triggered explicitly via
    # the "Auto-Geolocate Towers" button / geolocate-towers endpoint.
    if auto_geolocate:
        try:
            from services.geolocation import resolve_case_towers
            resolve_case_towers(db, case_id, use_opencellid=False)
        except Exception:
            pass

    towers = {t.cell_id: t for t in db.query(CellTower).filter(CellTower.case_id == case_id).all()}

    # Cluster consecutive same-cell records into dwell periods.
    # For each dwell we also tally direction (incoming vs outgoing) and SMS vs
    # voice so the map can show what kind of activity happened at each tower.
    def _direction_of(c):
        """Incoming/outgoing from the QUERIED number's point of view.

        The caller/callee columns are absolute, so we infer direction from them
        relative to the number actually typed in the box. This is important for
        cross-reference lookups where the queried number is NOT the file's own
        Subject: the stored `direction`/Call Type is relative to that subject and
        would be wrong here. We only fall back to the stored direction when the
        queried number can't be matched to caller/callee (e.g. subject_id rows)."""
        if c.caller == subject_id:
            return "outgoing"
        if c.callee == subject_id:
            return "incoming"
        # Queried number is the record's subject (not caller/callee) — trust the
        # stored direction, which is relative to that subject.
        if c.direction in ("incoming", "outgoing"):
            return c.direction
        # Stored SMS sub-types: MO = mobile-originated (out), MT = terminated (in).
        if c.direction:
            dl = c.direction.lower()
            if "mo" in dl:
                return "outgoing"
            if "mt" in dl:
                return "incoming"
        return "unknown"

    def _new_dwell(c, t):
        return {"cell_id": c.cell_id, "start": t, "end": t, "event_count": 1,
                "roaming_center": c.roaming_center,
                "incoming": 0, "outgoing": 0, "unknown": 0, "voice": 0, "sms": 0}

    def _tally(d, c):
        d[_direction_of(c)] += 1
        d["sms" if c.event_type == "sms" else "voice"] += 1

    dwells = []
    current = None
    for c in cdrs:
        t = c.normalized_time or c.start_time
        if current and current["cell_id"] == c.cell_id:
            current["end"] = t
            current["event_count"] += 1
            _tally(current, c)
        else:
            if current:
                dwells.append(current)
            current = _new_dwell(c, t)
            _tally(current, c)
    if current:
        dwells.append(current)

    has_coords = False
    out = []
    for d in dwells:
        tower = towers.get(d["cell_id"])
        entry = {
            "cell_id": d["cell_id"],
            "start": d["start"].isoformat(),
            "end": d["end"].isoformat(),
            "duration_minutes": round((d["end"] - d["start"]).total_seconds() / 60, 1),
            "event_count": d["event_count"],
            "incoming": d["incoming"],
            "outgoing": d["outgoing"],
            "voice": d["voice"],
            "sms": d["sms"],
            # The dominant direction drives the marker colour on the map.
            "primary_direction": ("outgoing" if d["outgoing"] > d["incoming"]
                                  else "incoming" if d["incoming"] > d["outgoing"]
                                  else "mixed"),
            "roaming_center": d.get("roaming_center"),
        }
        if tower:
            entry["lat"] = tower.latitude
            entry["lon"] = tower.longitude
            entry["address"] = tower.address
            entry["geo_source"] = tower.source
            entry["geo_confidence"] = tower.confidence
            has_coords = True
        out.append(entry)

    # Overall confidence reflects the weakest geolocation source in play.
    sources = {t.source for t in towers.values() if t.cell_id in {d["cell_id"] for d in dwells}}
    if "reference_csv" in sources or "opencellid" in sources:
        confidence = "medium"
    elif "roaming_centroid" in sources:
        confidence = "low"
    else:
        confidence = "low"

    return {"subject_id": subject_id, "dwell_periods": out,
            "has_coordinates": has_coords, "confidence": confidence,
            "geo_sources": sorted(s for s in sources if s)}


# ---------------------------------------------------------------------------
# NEW: Cell-site activity — who was active on a given cell tower, and which
# other cells those numbers connect to (Feature 8)
# ---------------------------------------------------------------------------

def list_case_cells(db: Session, case_id: int):
    """List every cell_id seen in the case with an event count and location
    label, so the UI can offer a picker. Includes both first- and last-cell
    (handover) values."""
    rows = db.query(CDRRecordDB).filter(CDRRecordDB.case_id == case_id).all()

    counts = {}
    roaming = {}
    for r in rows:
        for cid in (r.cell_id, r.last_cell_id):
            if not cid:
                continue
            counts[cid] = counts.get(cid, 0) + 1
            if cid not in roaming and r.roaming_center:
                roaming[cid] = r.roaming_center

    towers = {t.cell_id: t for t in
              db.query(CellTower).filter(CellTower.case_id == case_id).all()}

    cells = []
    for cid, n in counts.items():
        t = towers.get(cid)
        cells.append({
            "cell_id": cid,
            "events": n,
            "location": (t.address if t and t.address else roaming.get(cid)),
        })
    cells.sort(key=lambda c: c["events"], reverse=True)
    return {"cells": cells, "total": len(cells)}


def get_cell_activity(db: Session, case_id: int, cell_id: str):
    """For a specific cell tower: which numbers were active on it, how many
    calls each made (incoming/outgoing/voice/sms), and which OTHER cells those
    same numbers connect to (handovers from this cell + other cells they use).

    A record "touches" the cell if it is either the first cell (cell_id) or the
    last/handover cell (last_cell_id)."""
    rows = db.query(CDRRecordDB).filter(
        CDRRecordDB.case_id == case_id,
        ((CDRRecordDB.cell_id == cell_id) |
         (CDRRecordDB.last_cell_id == cell_id)),
    ).order_by(CDRRecordDB.normalized_time.asc()).all()

    if not rows:
        return {"error": f"No activity found on cell '{cell_id}' in this case"}

    def _num(r):
        # The party the record belongs to (subject viewpoint).
        return r.subject_id or r.caller

    def _dir(r):
        n = _num(r)
        if r.caller == n:
            return "outgoing"
        if r.callee == n:
            return "incoming"
        if r.direction in ("incoming", "outgoing"):
            return r.direction
        return "unknown"

    # Per-number activity on this cell.
    numbers = {}      # num -> stats dict

    for r in rows:
        n = _num(r)
        if not n:
            continue
        s = numbers.setdefault(n, {
            "number": n, "events": 0, "incoming": 0, "outgoing": 0,
            "voice": 0, "sms": 0, "contacts": set(), "imeis": set(),
            "first": None, "last": None,
        })
        s["events"] += 1
        s[_dir(r)] = s.get(_dir(r), 0) + 1
        s["sms" if r.event_type == "sms" else "voice"] += 1
        other = r.callee if r.caller == n else r.caller
        if other and other != n:
            s["contacts"].add(other)
        if r.imei:
            s["imeis"].add(r.imei)
        t = r.normalized_time or r.start_time
        if t:
            if s["first"] is None or t < s["first"]:
                s["first"] = t
            if s["last"] is None or t > s["last"]:
                s["last"] = t

    active_numbers = set(numbers.keys())

    # Connected cells + handovers are derived from ALL records of the active
    # numbers across the whole case (not just rows on this cell). This is what
    # actually answers "which other cells are these numbers connected to" — a
    # single record often has identical first/last cell, so intra-record logic
    # would show nothing.
    connected = {}    # other_cell_id -> {events, numbers:set}
    handovers = {}    # (from,to) -> count  (movement transitions to/from this cell)

    if active_numbers:
        all_rows = db.query(CDRRecordDB).filter(
            CDRRecordDB.case_id == case_id,
            ((CDRRecordDB.subject_id.in_(active_numbers)) |
             (CDRRecordDB.caller.in_(active_numbers)) |
             (CDRRecordDB.callee.in_(active_numbers))),
        ).order_by(CDRRecordDB.normalized_time.asc()).all()

        # Group each number's cell touches in time order to detect transitions.
        seq = {}  # num -> list of (time, cell)
        for r in all_rows:
            n = _num(r)
            if n not in active_numbers:
                # The active number might be the callee on this record.
                if r.callee in active_numbers:
                    n = r.callee
                elif r.caller in active_numbers:
                    n = r.caller
                else:
                    continue

            # Collect every distinct cell on this record.
            cells_here = [c for c in (r.cell_id, r.last_cell_id) if c]
            for cid in cells_here:
                if cid != cell_id:
                    c = connected.setdefault(cid, {"cell_id": cid, "events": 0, "numbers": set()})
                    c["events"] += 1
                    c["numbers"].add(n)
                # Within-record handover (first != last).
                if r.cell_id and r.last_cell_id and r.cell_id != r.last_cell_id \
                        and (r.cell_id == cell_id or r.last_cell_id == cell_id):
                    handovers[(r.cell_id, r.last_cell_id)] = \
                        handovers.get((r.cell_id, r.last_cell_id), 0) + 1

            t = r.normalized_time or r.start_time
            primary_cell = r.cell_id or r.last_cell_id
            if t and primary_cell:
                seq.setdefault(n, []).append((t, primary_cell))

        # Sequential transitions: consecutive records where the cell changes and
        # the queried cell is one of the two endpoints.
        for n, touches in seq.items():
            touches.sort(key=lambda x: x[0])
            for (t1, c1), (t2, c2) in zip(touches, touches[1:]):
                if c1 != c2 and (c1 == cell_id or c2 == cell_id):
                    handovers[(c1, c2)] = handovers.get((c1, c2), 0) + 1

    towers = {t.cell_id: t for t in
              db.query(CellTower).filter(CellTower.case_id == case_id).all()}

    def _loc(cid):
        t = towers.get(cid)
        return (t.address if t and t.address else None)

    numbers_out = []
    for s in numbers.values():
        primary = ("outgoing" if s["outgoing"] > s["incoming"]
                   else "incoming" if s["incoming"] > s["outgoing"] else "mixed")
        numbers_out.append({
            "number": s["number"], "events": s["events"],
            "incoming": s["incoming"], "outgoing": s["outgoing"],
            "voice": s["voice"], "sms": s["sms"],
            "unique_contacts": len(s["contacts"]),
            "handsets": sorted(s["imeis"]),
            "primary_direction": primary,
            "first_seen": (s["first"].isoformat() if s["first"] else None),
            "last_seen": (s["last"].isoformat() if s["last"] else None),
        })
    numbers_out.sort(key=lambda x: x["events"], reverse=True)

    connected_out = [{
        "cell_id": c["cell_id"], "events": c["events"],
        "number_count": len(c["numbers"]),
        "numbers": sorted(c["numbers"]),
        "location": _loc(c["cell_id"]),
    } for c in connected.values()]
    connected_out.sort(key=lambda x: x["events"], reverse=True)

    handover_out = [{
        "from": f, "to": t, "count": n,
        "from_location": _loc(f), "to_location": _loc(t),
    } for (f, t), n in sorted(handovers.items(), key=lambda kv: kv[1], reverse=True)]

    this_tower = towers.get(cell_id)
    times = [r.normalized_time or r.start_time for r in rows
             if (r.normalized_time or r.start_time)]

    return {
        "cell_id": cell_id,
        "location": (this_tower.address if this_tower and this_tower.address else
                     (rows[0].roaming_center if rows[0].roaming_center else None)),
        "lat": (this_tower.latitude if this_tower else None),
        "lon": (this_tower.longitude if this_tower else None),
        "total_events": len(rows),
        "unique_numbers": len(numbers_out),
        "first_seen": (min(times).isoformat() if times else None),
        "last_seen": (max(times).isoformat() if times else None),
        "numbers": numbers_out,
        "connected_cells": connected_out,
        "handovers": handover_out,
    }


# ---------------------------------------------------------------------------
# NEW: Entity (phone number) drill-down details for graph node clicks
# ---------------------------------------------------------------------------

def get_entity_details(db: Session, case_id: int, number: str):
    """Everything known about one phone number: interaction counts, the network
    operator(s) it used, handsets (IMEIs), cells seen, and top contacts."""
    from services.enrichment import operator_from_imsi

    rows = db.query(CDRRecordDB).filter(
        CDRRecordDB.case_id == case_id,
        ((CDRRecordDB.caller == number) | (CDRRecordDB.callee == number))
    ).order_by(CDRRecordDB.normalized_time.asc()).all()

    if not rows:
        return {"error": f"No records found for '{number}' in this case"}

    outgoing = sum(1 for r in rows if r.caller == number)
    incoming = sum(1 for r in rows if r.callee == number)
    voice = sum(1 for r in rows if (r.event_type or "voice") == "voice")
    sms = sum(1 for r in rows if r.event_type == "sms")
    total_duration = sum(r.duration or 0 for r in rows)

    # Operators used (derived from IMSI of records where this number is the subject/caller)
    operators = {}
    imsis = set()
    for r in rows:
        if r.imsi and (r.caller == number or r.subject_id == number):
            imsis.add(r.imsi)
    for imsi in imsis:
        info = operator_from_imsi(imsi)
        name = info.get("operator") or "Unknown"
        operators[name] = operators.get(name, 0) + 1

    # Handsets (IMEIs) used by this number
    imeis = {}
    for r in rows:
        if r.imei and (r.caller == number or r.subject_id == number):
            imeis[r.imei] = imeis.get(r.imei, 0) + 1

    # Cells seen
    cells = {}
    for r in rows:
        if r.cell_id and (r.caller == number or r.subject_id == number):
            cells[r.cell_id] = cells.get(r.cell_id, 0) + 1

    # Top contacts
    contacts = {}
    for r in rows:
        other = r.callee if r.caller == number else r.caller
        if other and other != number:
            contacts[other] = contacts.get(other, 0) + 1
    top_contacts = sorted(contacts.items(), key=lambda kv: kv[1], reverse=True)[:10]

    # Roaming centres / locations touched
    roaming = {}
    for r in rows:
        if r.roaming_center and (r.caller == number or r.subject_id == number):
            roaming[r.roaming_center] = roaming.get(r.roaming_center, 0) + 1

    times = [r.normalized_time or r.start_time for r in rows if (r.normalized_time or r.start_time)]

    return {
        "number": number,
        "total_interactions": len(rows),
        "outgoing": outgoing,
        "incoming": incoming,
        "voice": voice,
        "sms": sms,
        "total_duration_sec": total_duration,
        "unique_contacts": len(contacts),
        "operators": [{"operator": k, "records": v} for k, v in
                      sorted(operators.items(), key=lambda kv: kv[1], reverse=True)],
        "primary_operator": (max(operators, key=operators.get) if operators else "Unknown"),
        "imeis": [{"imei": k, "records": v} for k, v in
                  sorted(imeis.items(), key=lambda kv: kv[1], reverse=True)],
        "cells": [{"cell_id": k, "records": v} for k, v in
                  sorted(cells.items(), key=lambda kv: kv[1], reverse=True)],
        "roaming_centers": [{"location": k, "records": v} for k, v in
                            sorted(roaming.items(), key=lambda kv: kv[1], reverse=True)],
        "top_contacts": [{"number": k, "interactions": v} for k, v in top_contacts],
        "first_seen": (min(times).isoformat() if times else None),
        "last_seen": (max(times).isoformat() if times else None),
    }


def get_imei_details(db: Session, case_id: int, imei: str):
    """Phone (handset) details for an IMEI plus which subjects/SIMs used it."""
    from services.enrichment import imei_details, operator_from_imsi

    details = imei_details(imei)

    rows = db.query(CDRRecordDB).filter(
        CDRRecordDB.case_id == case_id, CDRRecordDB.imei == imei
    ).order_by(CDRRecordDB.normalized_time.asc()).all()

    subjects, imsis = {}, {}
    for r in rows:
        who = r.subject_id or r.caller
        if who:
            subjects[who] = subjects.get(who, 0) + 1
        if r.imsi:
            imsis[r.imsi] = imsis.get(r.imsi, 0) + 1

    sim_list = []
    for imsi, cnt in sorted(imsis.items(), key=lambda kv: kv[1], reverse=True):
        info = operator_from_imsi(imsi)
        sim_list.append({"imsi": imsi, "records": cnt,
                         "operator": info.get("operator") or "Unknown",
                         "country": info.get("country")})

    times = [r.normalized_time or r.start_time for r in rows if (r.normalized_time or r.start_time)]

    return {
        **details,
        "usage_records": len(rows),
        "used_by_subjects": [{"number": k, "records": v} for k, v in
                             sorted(subjects.items(), key=lambda kv: kv[1], reverse=True)],
        "sims_used": sim_list,
        "sim_swap_suspected": len(imsis) > 1,
        "first_seen": (min(times).isoformat() if times else None),
        "last_seen": (max(times).isoformat() if times else None),
    }


# ---------------------------------------------------------------------------
# NEW: Case summary statistics
# ---------------------------------------------------------------------------

def get_case_summary(db: Session, case_id: int):
    cdr_count = db.query(CDRRecordDB).filter(CDRRecordDB.case_id == case_id).count()

    subjects = set()
    for (s,) in db.query(CDRRecordDB.caller).filter(CDRRecordDB.case_id == case_id).distinct():
        subjects.add(s)
    for (s,) in db.query(CDRRecordDB.callee).filter(CDRRecordDB.case_id == case_id).distinct():
        subjects.add(s)

    date_range = {}
    first = (db.query(CDRRecordDB.normalized_time).filter(CDRRecordDB.case_id == case_id)
             .order_by(CDRRecordDB.normalized_time.asc()).first())
    last = (db.query(CDRRecordDB.normalized_time).filter(CDRRecordDB.case_id == case_id)
            .order_by(CDRRecordDB.normalized_time.desc()).first())
    if first and first[0]:
        date_range = {"first_event": first[0].isoformat(), "last_event": last[0].isoformat()}

    anomaly_count = db.query(AnomalyFlag).filter(AnomalyFlag.case_id == case_id).count()
    upload_count = db.query(EvidenceLog).filter(EvidenceLog.case_id == case_id,
                                                EvidenceLog.action == "uploaded").count()

    return {
        "cdr_count": cdr_count,
        "unique_entities": len(subjects),
        "anomaly_count": anomaly_count,
        "files_uploaded": upload_count,
        **date_range,
    }


# ---------------------------------------------------------------------------
# NEW: Multi-CDR cross analysis — insights across several uploaded datasets
# ---------------------------------------------------------------------------

def get_cross_analysis(db: Session, case_id: int, top_n: int = 25):
    """Analyse every CDR ingested into a case (typically several operator dumps
    of different suspects) and surface links between them:
      * common numbers contacted by more than one subject (bridges)
      * shared handsets (same IMEI used across different numbers) -> SIM swap
      * shared SIMs (same IMSI in different handsets)
      * shared cell towers (co-location)
      * busiest talkers and most-contacted numbers
    """
    from services.enrichment import operator_from_imsi

    rows = db.query(CDRRecordDB).filter(CDRRecordDB.case_id == case_id).all()
    if not rows:
        return {"error": "No CDR data in this case", "record_count": 0}

    # A "subject" is the owner of a record — prefer subject_id, else caller.
    def subject_of(r):
        return r.subject_id or r.caller

    subjects = set()
    contact_to_subjects = {}      # number -> set(subjects that contacted it)
    imei_to_numbers = {}          # imei -> set(numbers seen on it)
    imsi_to_imeis = {}            # imsi -> set(imeis it was used in)
    cell_to_subjects = {}         # cell_id -> set(subjects seen there)
    talk_out = {}                 # subject -> outgoing count
    contacted = {}                # number -> times it was the callee

    for r in rows:
        subj = subject_of(r)
        if subj:
            subjects.add(subj)
            talk_out[subj] = talk_out.get(subj, 0) + 1

        # who did this subject talk to
        other = r.callee if (r.caller == subj) else r.caller
        if other and other != subj:
            contact_to_subjects.setdefault(other, set()).add(subj)
            contacted[other] = contacted.get(other, 0) + 1

        if r.imei:
            # The IMEI belongs to the subject who owns this record, not to
            # r.caller (which is the remote party on incoming rows).
            num = subj or r.caller
            if num:
                imei_to_numbers.setdefault(r.imei, set()).add(num)
        if r.imsi and r.imei:
            imsi_to_imeis.setdefault(r.imsi, set()).add(r.imei)
        if r.cell_id and subj:
            cell_to_subjects.setdefault(r.cell_id, set()).add(subj)

    # Common numbers: contacted by 2+ distinct subjects (potential coordinators)
    common_numbers = []
    for num, subs in contact_to_subjects.items():
        if len(subs) >= 2:
            common_numbers.append({
                "number": num,
                "shared_by": sorted(subs),
                "subject_count": len(subs),
                "total_contacts": contacted.get(num, 0),
            })
    common_numbers.sort(key=lambda x: (x["subject_count"], x["total_contacts"]), reverse=True)

    # Shared handsets: one IMEI used by 2+ different numbers -> SIM swapping
    shared_handsets = []
    for imei, nums in imei_to_numbers.items():
        if len(nums) >= 2:
            shared_handsets.append({
                "imei": imei,
                "numbers": sorted(nums),
                "number_count": len(nums),
            })
    shared_handsets.sort(key=lambda x: x["number_count"], reverse=True)

    # Shared SIMs: one IMSI seen in 2+ handsets -> SIM moved between phones
    shared_sims = []
    for imsi, imeis in imsi_to_imeis.items():
        if len(imeis) >= 2:
            info = operator_from_imsi(imsi)
            shared_sims.append({
                "imsi": imsi,
                "operator": info.get("operator") or "Unknown",
                "imeis": sorted(imeis),
                "imei_count": len(imeis),
            })
    shared_sims.sort(key=lambda x: x["imei_count"], reverse=True)

    # Shared cells: a tower used by 2+ subjects -> possible co-location
    shared_cells = []
    for cell, subs in cell_to_subjects.items():
        if len(subs) >= 2:
            shared_cells.append({
                "cell_id": cell,
                "subjects": sorted(subs),
                "subject_count": len(subs),
            })
    shared_cells.sort(key=lambda x: x["subject_count"], reverse=True)

    busiest = sorted(talk_out.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    most_contacted = sorted(contacted.items(), key=lambda kv: kv[1], reverse=True)[:top_n]

    return {
        "record_count": len(rows),
        "subject_count": len(subjects),
        "subjects": sorted(subjects)[:200],
        "common_numbers": common_numbers[:top_n],
        "shared_handsets": shared_handsets[:top_n],
        "shared_sims": shared_sims[:top_n],
        "shared_cells": shared_cells[:top_n],
        "busiest_talkers": [{"number": k, "records": v} for k, v in busiest],
        "most_contacted": [{"number": k, "contacts": v} for k, v in most_contacted],
        "totals": {
            "common_numbers": len(common_numbers),
            "shared_handsets": len(shared_handsets),
            "shared_sims": len(shared_sims),
            "shared_cells": len(shared_cells),
        },
    }


# ---------------------------------------------------------------------------
# NEW: IMEI / SIM-swap graph — one node per handset (IMEI) and per number,
# edges = "this number was used on this handset". A handset linked to several
# numbers is a SIM-swap cluster; a number linked to several handsets used
# multiple phones.
# ---------------------------------------------------------------------------

def get_imei_graph(db: Session, case_id: int):
    from services.enrichment import operator_from_imsi, imei_details

    rows = db.query(CDRRecordDB).filter(
        CDRRecordDB.case_id == case_id,
        CDRRecordDB.imei.isnot(None),
    ).all()

    if not rows:
        return {"nodes": [], "edges": [], "swap_clusters": [], "stats": {}}

    # Aggregate edge weights between (number, imei) and gather per-node info.
    #
    # A handset (IMEI) belongs to the record's SUBJECT — the phone whose CDR this
    # is — NOT to r.caller. On an *incoming* record the caller is the remote
    # party, so keying the IMEI to r.caller would wrongly attach the handset to
    # everyone who ever rang the subject, making every IMEI look SIM-swapped.
    # The subject owns the IMEI/IMSI printed on the row, so key by subject.
    edge_w = {}                      # (number, imei) -> count
    imei_numbers = {}                # imei -> set(subject numbers)
    number_imeis = {}                # number -> set(imeis)
    imei_imsis = {}                  # imei -> set(imsis)
    number_operator = {}             # number -> operator (from imsi)

    for r in rows:
        num = r.subject_id or r.caller
        if not num or not r.imei:
            continue
        edge_w[(num, r.imei)] = edge_w.get((num, r.imei), 0) + 1
        imei_numbers.setdefault(r.imei, set()).add(num)
        number_imeis.setdefault(num, set()).add(r.imei)
        if r.imsi:
            imei_imsis.setdefault(r.imei, set()).add(r.imsi)
            if num not in number_operator:
                number_operator[num] = operator_from_imsi(r.imsi).get("operator") or "Unknown"

    nodes = []
    for num, imeis in number_imeis.items():
        nodes.append({
            "id": f"num:{num}",
            "label": num,
            "type": "number",
            "operator": number_operator.get(num, "Unknown"),
            "handset_count": len(imeis),
        })
    for imei, nums in imei_numbers.items():
        dev = imei_details(imei)
        nodes.append({
            "id": f"imei:{imei}",
            "label": imei,
            "type": "imei",
            "make_model": dev.get("make_model"),
            "number_count": len(nums),
            "sim_count": len(imei_imsis.get(imei, set())),
            "swap": len(nums) >= 2,          # multiple numbers on one handset
        })

    edges = []
    for (num, imei), w in edge_w.items():
        edges.append({
            "id": f"e:{num}:{imei}",
            "source": f"num:{num}",
            "target": f"imei:{imei}",
            "weight": w,
        })

    # SIM-swap clusters: an IMEI touched by 2+ numbers, with all those numbers.
    swap_clusters = []
    for imei, nums in imei_numbers.items():
        if len(nums) >= 2:
            dev = imei_details(imei)
            swap_clusters.append({
                "imei": imei,
                "make_model": dev.get("make_model"),
                "numbers": sorted(nums),
                "number_count": len(nums),
                "sim_count": len(imei_imsis.get(imei, set())),
            })
    swap_clusters.sort(key=lambda c: c["number_count"], reverse=True)

    return {
        "nodes": nodes,
        "edges": edges,
        "swap_clusters": swap_clusters,
        "stats": {
            "numbers": len(number_imeis),
            "handsets": len(imei_numbers),
            "swap_handsets": len(swap_clusters),
        },
    }


# ---------------------------------------------------------------------------
# NEW: Delete a case and every record attached to it
# ---------------------------------------------------------------------------

def delete_case(db: Session, case_id: int):
    """Permanently delete a case and every row attached to it. Wrapped in a
    try/rollback so a failure returns a real error message instead of leaving
    the session in a broken state (which is what makes delete silently 'not
    work' in the UI). Also clears the ipdrs table by raw SQL — it has no ORM
    model but does carry a case_id FK, so it must be cleaned too."""
    from sqlalchemy import text

    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        return {"deleted": False, "error": "Case not found"}

    name = case.case_name
    try:
        counts = {
            "cdrs": db.query(CDRRecordDB).filter(CDRRecordDB.case_id == case_id).delete(synchronize_session=False),
            "cell_towers": db.query(CellTower).filter(CellTower.case_id == case_id).delete(synchronize_session=False),
            "anomaly_flags": db.query(AnomalyFlag).filter(AnomalyFlag.case_id == case_id).delete(synchronize_session=False),
            "evidence_logs": db.query(EvidenceLog).filter(EvidenceLog.case_id == case_id).delete(synchronize_session=False),
        }
        # ipdrs has a case_id FK but no ORM model — clear it defensively so a
        # future FK-enforcing build can't block the case delete.
        try:
            res = db.execute(text("DELETE FROM ipdrs WHERE case_id = :cid"), {"cid": case_id})
            counts["ipdrs"] = res.rowcount if res.rowcount and res.rowcount > 0 else 0
        except Exception:
            counts["ipdrs"] = 0

        db.delete(case)
        db.commit()
        return {"deleted": True, "case_id": case_id, "case_name": name, "removed": counts}
    except Exception as e:
        db.rollback()
        return {"deleted": False, "error": f"Delete failed: {e}"}
