"""
Cell-tower geolocation resolver for movement reconstruction.

The platform reconstructs a subject's movement from the sequence of cell-tower
IDs in their CDR. To place those towers on a map we need latitude/longitude for
each cell_id. This resolver uses a layered strategy so it works for both real
operator data and synthetic/mock cell IDs:

  1. Reference CSV  — coordinates uploaded manually (highest trust).
  2. OpenCellID API — looked up when the cell_id (optionally combined with the
                      MCC/MNC derived from the IMSI) can be resolved to a real
                      tower. Requires network access + a valid API key.
  3. Roaming-centre centroid — when the cell_id is opaque/synthetic (e.g.
                      "CELL-1003"), we anchor it to the city of the serving MSC
                      (Roaming Center column) and apply a small deterministic
                      per-cell offset so distinct cells don't overlap. This is
                      an APPROXIMATION and is tagged low confidence.

Every resolved coordinate is cached in the cell_towers table with a `source`
and `confidence` so the UI and PDF report can be honest about provenance.
"""

import os
import re
import json
import hashlib
import urllib.request
import urllib.parse
from typing import Optional, Dict, Tuple

from sqlalchemy.orm import Session

from models.database import CDRRecordDB, CellTower

# OpenCellID API key (provided by the investigator). Env var overrides the default.
OPENCELLID_API_KEY = os.environ.get("OPENCELLID_API_KEY", "pk.8121908c7e7db163887b3a531db75690")
OPENCELLID_URL = "https://opencellid.org/cell/get"

# Approximate city centroids for the Multi-Switching-Centre (MSC) / roaming-centre
# location signal present in Indian operator CDR exports. Used only as a
# low-confidence fallback when a cell_id cannot be resolved to real coordinates.
MSC_CENTROIDS: Dict[str, Tuple[float, float]] = {
    "delhi":      (28.6139, 77.2090),
    "mumbai":     (19.0760, 72.8777),
    "lucknow":    (26.8467, 80.9462),
    "varanasi":   (25.3176, 82.9739),
    "gorakhpur":  (26.7606, 83.3732),
    "kanpur":     (26.4499, 80.3319),
    "kolkata":    (22.5726, 88.3639),
    "chennai":    (13.0827, 80.2707),
    "bangalore":  (12.9716, 77.5946),
    "bengaluru":  (12.9716, 77.5946),
    "hyderabad":  (17.3850, 78.4867),
    "pune":       (18.5204, 73.8567),
    "ahmedabad":  (23.0225, 72.5714),
    "jaipur":     (26.9124, 75.7873),
    "patna":      (25.5941, 85.1376),
    "allahabad":  (25.4358, 81.8463),
    "prayagraj":  (25.4358, 81.8463),
    "noida":      (28.5355, 77.3910),
    "agra":       (27.1767, 78.0081),
    "gurgaon":    (28.4595, 77.0266),
    "gurugram":   (28.4595, 77.0266),
    "faridabad":  (28.4089, 77.3178),
    "ghaziabad":  (28.6692, 77.4538),
    "greater noida": (28.4744, 77.5040),
}


def _city_from_roaming(roaming_center: Optional[str]) -> Optional[str]:
    if not roaming_center:
        return None
    key = roaming_center.strip().lower()
    # Strip trailing "msc" / "mss" / "center"/"centre" tokens: "Delhi MSC" -> "delhi"
    key = re.sub(r"\b(msc|mss|center|centre|bts|node)\b", "", key).strip()
    key = key.strip(" -_")
    if key in MSC_CENTROIDS:
        return key
    # Substring scan: place strings like "Nehru Place, New Delhi" or
    # "Cyber City, Gurgaon" contain a known city name even though the first
    # token isn't the city. Match the longest city name found so "greater
    # noida" wins over "noida". Word-boundary guarded to avoid partial hits.
    matches = [c for c in MSC_CENTROIDS
               if re.search(r"\b" + re.escape(c) + r"\b", key)]
    if matches:
        return max(matches, key=len)
    # Try first word (e.g. "Delhi North MSC" -> "delhi")
    first = key.split()[0] if key.split() else ""
    return first if first in MSC_CENTROIDS else None


def _deterministic_offset(cell_id: str, spread_deg: float = 0.045) -> Tuple[float, float]:
    """Stable pseudo-random lat/lon offset (~a few km) derived from the cell_id
    so that distinct synthetic cells in the same city don't stack on one point.
    Deterministic => the same cell always lands in the same spot."""
    h = hashlib.md5(cell_id.encode()).hexdigest()
    a = int(h[:8], 16) / 0xFFFFFFFF   # 0..1
    b = int(h[8:16], 16) / 0xFFFFFFFF
    return (a - 0.5) * 2 * spread_deg, (b - 0.5) * 2 * spread_deg


def _parse_real_cell(cell_id: str) -> Optional[Dict[str, int]]:
    """If a cell_id encodes real network identifiers we can query OpenCellID.
    Accepts forms like 'MCC-MNC-LAC-CID', 'MCC:MNC:LAC:CID' or a bare numeric
    CID (which we pair with IMSI-derived MCC/MNC at the call site)."""
    parts = re.split(r"[-_:/ ]+", cell_id.strip())
    nums = [p for p in parts if p.isdigit()]
    if len(nums) >= 4:
        return {"mcc": int(nums[0]), "mnc": int(nums[1]),
                "lac": int(nums[2]), "cellid": int(nums[3])}
    return None


def _mcc_mnc_from_imsi(imsi: Optional[str]) -> Optional[Tuple[int, int]]:
    if not imsi or not imsi.isdigit() or len(imsi) < 5:
        return None
    # MCC is 3 digits; MNC is 2 (most of the world, incl. India) or 3.
    return int(imsi[:3]), int(imsi[3:5])


def _query_opencellid(mcc: int, mnc: int, lac: int, cellid: int,
                      timeout: float = 6.0) -> Optional[Tuple[float, float]]:
    """Query OpenCellID for a single cell. Returns (lat, lon) or None.
    Network failures are swallowed — geolocation is best-effort."""
    params = urllib.parse.urlencode({
        "key": OPENCELLID_API_KEY, "mcc": mcc, "mnc": mnc,
        "lac": lac, "cellid": cellid, "format": "json",
    })
    url = f"{OPENCELLID_URL}?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CDR-Analysis-Platform/2.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        lat = payload.get("lat")
        lon = payload.get("lon")
        if lat in (None, 0) and lon in (None, 0):
            return None
        return float(lat), float(lon)
    except Exception:
        return None


def resolve_case_towers(db: Session, case_id: int, use_opencellid: bool = True) -> Dict:
    """Resolve coordinates for every distinct cell_id in a case and cache them.
    Never overwrites reference-CSV coordinates. Returns a small summary dict."""
    # Distinct (cell_id) with a representative imsi + roaming_center for context.
    rows = (db.query(CDRRecordDB.cell_id, CDRRecordDB.imsi, CDRRecordDB.roaming_center)
            .filter(CDRRecordDB.case_id == case_id, CDRRecordDB.cell_id.isnot(None))
            .all())
    # also fold in last_cell_id
    last_rows = (db.query(CDRRecordDB.last_cell_id, CDRRecordDB.imsi, CDRRecordDB.roaming_center)
                 .filter(CDRRecordDB.case_id == case_id, CDRRecordDB.last_cell_id.isnot(None))
                 .all())

    context: Dict[str, Dict] = {}
    for cell_id, imsi, rc in list(rows) + list(last_rows):
        if not cell_id:
            continue
        ctx = context.setdefault(cell_id, {"imsi": None, "roaming": None})
        if imsi and not ctx["imsi"]:
            ctx["imsi"] = imsi
        if rc and not ctx["roaming"]:
            ctx["roaming"] = rc

    existing = {t.cell_id: t for t in
                db.query(CellTower).filter(CellTower.case_id == case_id).all()}

    stats = {"total_cells": len(context), "reference": 0, "opencellid": 0,
             "roaming_centroid": 0, "unresolved": 0, "newly_resolved": 0}

    for cell_id, ctx in context.items():
        prior = existing.get(cell_id)
        # Preserve manually-uploaded reference coordinates untouched.
        if prior and prior.source in (None, "reference_csv"):
            stats["reference"] += 1
            continue
        # An already-cached OpenCellID hit is authoritative — keep it.
        if prior and prior.source == "opencellid":
            stats["opencellid"] += 1
            continue
        # A cached roaming-centroid is only kept if we're NOT now allowed to try
        # OpenCellID (which would be a higher-confidence upgrade).
        if prior and prior.source == "roaming_centroid" and not use_opencellid:
            stats["roaming_centroid"] += 1
            continue

        lat = lon = None
        source = None
        confidence = None

        # (2) OpenCellID for real cell identifiers
        if use_opencellid:
            parsed = _parse_real_cell(cell_id)
            if not parsed:
                mm = _mcc_mnc_from_imsi(ctx.get("imsi"))
                bare = re.sub(r"\D", "", cell_id)
                if mm and bare:
                    parsed = {"mcc": mm[0], "mnc": mm[1], "lac": 0, "cellid": int(bare)}
            if parsed:
                coords = _query_opencellid(**parsed)
                if coords:
                    lat, lon = coords
                    source, confidence = "opencellid", "high"

        # (3) Roaming-centre centroid fallback
        if lat is None:
            # If a centroid is already cached and OpenCellID didn't improve it, keep it.
            if prior and prior.source == "roaming_centroid":
                stats["roaming_centroid"] += 1
                continue
            city = _city_from_roaming(ctx.get("roaming"))
            if city:
                base = MSC_CENTROIDS[city]
                d_lat, d_lon = _deterministic_offset(cell_id)
                lat, lon = base[0] + d_lat, base[1] + d_lon
                source, confidence = "roaming_centroid", "low"

        if lat is None:
            stats["unresolved"] += 1
            continue

        # Upgrade in place if a prior low-confidence row exists.
        if prior is not None:
            prior.latitude, prior.longitude = lat, lon
            prior.source, prior.confidence = source, confidence
            if source == "roaming_centroid":
                prior.address = ctx.get("roaming")
        else:
            db.add(CellTower(
                case_id=case_id, cell_id=cell_id, latitude=lat, longitude=lon,
                operator=None,
                address=(ctx.get("roaming") if source == "roaming_centroid" else None),
                source=source, confidence=confidence,
            ))
        stats[source] += 1
        stats["newly_resolved"] += 1

    db.commit()
    return stats
