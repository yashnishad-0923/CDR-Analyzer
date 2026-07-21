"""
Forensic report generation for the CDR Analysis Platform.

Produces court-oriented PDF reports (drafted for admissibility of electronic
evidence under Section 63 of the Bharatiya Sakshya Adhiniyam, 2023 / erstwhile
Section 65B of the Indian Evidence Act, 1872) with:
- Cover page (case metadata, evidence banner, report hash placeholder)
- Executive summary with key statistics
- Key actor (network centrality) analysis
- Detected anomalies with confidence tags
- Record excerpts
- Chain-of-custody appendix (Appendix A)
- Section 63 / 65B certificate for electronic records (Appendix B)
- Numbered pages, headers/footers, signature block
"""

import base64
import binascii
from io import BytesIO
from datetime import datetime, timezone

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether, Image,
)
from sqlalchemy.orm import Session

from models.database import Case
from services.ingestion import get_all_cdrs, log_action
from services.analysis import get_custody_log, get_anomalies, get_graph_metrics, get_case_summary

# ---------------------------------------------------------------------------
# Palette & styles
# ---------------------------------------------------------------------------

NAVY = colors.HexColor("#1a2744")
ACCENT = colors.HexColor("#2563eb")
LIGHT_ROW = colors.HexColor("#f1f5f9")
MID_GREY = colors.HexColor("#64748b")
BORDER = colors.HexColor("#cbd5e1")
RED = colors.HexColor("#b91c1c")
AMBER = colors.HexColor("#b45309")
GREEN = colors.HexColor("#15803d")

_base = getSampleStyleSheet()

STYLES = {
    "CoverTitle": ParagraphStyle("CoverTitle", parent=_base["Title"], fontName="Helvetica-Bold",
                                 fontSize=24, leading=30, textColor=NAVY, alignment=TA_CENTER),
    "CoverSub": ParagraphStyle("CoverSub", parent=_base["Normal"], fontSize=13, leading=18,
                               textColor=MID_GREY, alignment=TA_CENTER),
    "H1": ParagraphStyle("H1", parent=_base["Heading1"], fontName="Helvetica-Bold", fontSize=15,
                         textColor=NAVY, spaceBefore=18, spaceAfter=8),
    "H2": ParagraphStyle("H2", parent=_base["Heading2"], fontName="Helvetica-Bold", fontSize=12,
                         textColor=NAVY, spaceBefore=12, spaceAfter=6),
    "Body": ParagraphStyle("Body", parent=_base["Normal"], fontSize=9.5, leading=14,
                           textColor=colors.HexColor("#111827")),
    "CertBody": ParagraphStyle("CertBody", parent=_base["Normal"], fontSize=8.5, leading=11.5,
                               textColor=colors.HexColor("#111827")),
    "Small": ParagraphStyle("Small", parent=_base["Normal"], fontSize=8, leading=11, textColor=MID_GREY),
    "Cell": ParagraphStyle("Cell", parent=_base["Normal"], fontSize=8, leading=10),
    "Banner": ParagraphStyle("Banner", parent=_base["Normal"], fontName="Helvetica-Bold",
                             fontSize=10, textColor=colors.white, alignment=TA_CENTER),
}

CONF_COLORS = {"high": GREEN, "medium": AMBER, "low": MID_GREY}
SEV_COLORS = {"high": RED, "medium": AMBER, "low": MID_GREY}


# ---------------------------------------------------------------------------
# Page furniture
# ---------------------------------------------------------------------------

def _header_footer(report_title: str, case_label: str):
    def draw(canvas, doc):
        canvas.saveState()
        w, h = A4
        # Header
        canvas.setFillColor(NAVY)
        canvas.rect(0, h - 14 * mm, w, 14 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 8.5)
        canvas.drawString(18 * mm, h - 9 * mm, report_title)
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(w - 18 * mm, h - 9 * mm, case_label)
        # Sub-header strip: evidentiary notice (neutral, court-appropriate)
        canvas.setFillColor(MID_GREY)
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(w / 2, h - 13 * mm,
                                 "Electronic evidence — accompanied by certificate u/s 63 BSA, 2023 (formerly s.65B, Indian Evidence Act, 1872)")
        # Footer
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(18 * mm, 14 * mm, w - 18 * mm, 14 * mm)
        canvas.setFillColor(MID_GREY)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(18 * mm, 9 * mm,
                          f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · CDR Analysis Platform")
        canvas.drawRightString(w - 18 * mm, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def _make_doc(buffer, report_title, case_label):
    doc = BaseDocTemplate(buffer, pagesize=A4,
                          leftMargin=18 * mm, rightMargin=18 * mm,
                          topMargin=22 * mm, bottomMargin=20 * mm,
                          title=report_title)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame],
                                       onPage=_header_footer(report_title, case_label))])
    return doc


def _styled_table(data, col_widths=None, align_left=True):
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_ROW]),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "LEFT" if align_left else "CENTER"),
    ]
    t.setStyle(TableStyle(style))
    return t


def _conf_tag(level):
    level = (level or "low").lower()
    color = {"high": "#15803d", "medium": "#b45309", "low": "#64748b"}.get(level, "#64748b")
    return f'<font color="{color}"><b>[{level.upper()} CONFIDENCE]</b></font>'


def _kv_block(pairs):
    rows = [[Paragraph(f"<b>{k}</b>", STYLES["Cell"]), Paragraph(str(v), STYLES["Cell"])] for k, v in pairs]
    t = Table(rows, colWidths=[45 * mm, 115 * mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_ROW),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


# ---------------------------------------------------------------------------
# Shared sections
# ---------------------------------------------------------------------------

def _cover_page(elements, report_type, case, summary):
    elements.append(Spacer(1, 30 * mm))
    banner = Table([[Paragraph("FORENSIC EVIDENCE REPORT", STYLES["Banner"])]], colWidths=[90 * mm])
    banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY),
                                ("TOPPADDING", (0, 0), (-1, -1), 4),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    wrapper = Table([[banner]], colWidths=[174 * mm])
    wrapper.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    elements.append(wrapper)
    elements.append(Spacer(1, 14 * mm))
    elements.append(Paragraph(f"{report_type} Investigation Report", STYLES["CoverTitle"]))
    elements.append(Spacer(1, 4 * mm))
    elements.append(Paragraph("Telecommunications Records Analysis · Forensic Summary", STYLES["CoverSub"]))
    elements.append(Spacer(1, 16 * mm))

    case_name = case.case_name if case else f"Case {getattr(case, 'id', '?')}"
    pairs = [
        ("Case Name", case_name),
        ("Case / FIR Number", case.case_number if case else "N/A"),
        ("Case Status", (case.status or "open").upper() if case else "N/A"),
        ("Opened By", case.created_by if case else "N/A"),
        ("Case Opened", case.created_at.strftime("%Y-%m-%d %H:%M UTC") if case and case.created_at else "N/A"),
        ("Report Generated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
        ("Records In Scope", f"{summary.get('cdr_count', 0)} CDR"),
        ("Generated By", "CDR Analysis Platform v2.1"),
    ]
    elements.append(_kv_block(pairs))
    elements.append(Spacer(1, 16 * mm))
    elements.append(Paragraph(
        "This report was generated from Call Detail Records (CDR) produced by the telecom service "
        "provider and ingested into the CDR Analysis Platform. It is intended to be produced as "
        "electronic evidence and is accompanied by a certificate under Section 63 of the Bharatiya "
        "Sakshya Adhiniyam, 2023 (corresponding to the erstwhile Section 65B of the Indian Evidence "
        "Act, 1872) at Appendix B. Every analytical finding is tagged with a confidence level that "
        "distinguishes directly observed record data from derived or statistically inferred conclusions. "
        "The chain-of-custody appendix records the SHA-256 hash of each source file to enable independent "
        "verification of data integrity.", STYLES["Small"]))
    elements.append(PageBreak())


def _methodology_note(elements):
    elements.append(Paragraph("Methodology & Confidence Levels", STYLES["H2"]))
    elements.append(Paragraph(
        f"{_conf_tag('high')} — directly observed in raw source records (e.g., a literal CDR row).&nbsp;&nbsp;"
        f"{_conf_tag('medium')} — derived from normalized or corrected data (e.g., timezone-adjusted comparison, "
        f"graph centrality).&nbsp;&nbsp;"
        f"{_conf_tag('low')} — statistical inference (e.g., behavioral patterns, movement approximation). "
        "All timestamps are normalized to UTC unless stated otherwise.", STYLES["Body"]))
    elements.append(Spacer(1, 6))


def _anomaly_section(elements, anomalies):
    elements.append(Paragraph("Detected Anomalies & Anti-Forensic Indicators", STYLES["H1"]))
    if not anomalies:
        elements.append(Paragraph("No anomalies were flagged by the rule-based detection engine "
                                  "for the records in scope.", STYLES["Body"]))
        return
    data = [["#", "Subject", "Type", "Severity", "Confidence", "Description"]]
    for i, a in enumerate(anomalies, 1):
        data.append([
            str(i),
            Paragraph(a.subject_id or "—", STYLES["Cell"]),
            Paragraph((a.flag_type or "").replace("_", " ").title(), STYLES["Cell"]),
            Paragraph((a.severity or "—").upper(), STYLES["Cell"]),
            Paragraph((a.confidence or "—").upper(), STYLES["Cell"]),
            Paragraph(a.description or "", STYLES["Cell"]),
        ])
    elements.append(_styled_table(data, col_widths=[8 * mm, 24 * mm, 24 * mm, 18 * mm, 20 * mm, 80 * mm]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "Each flag above is produced by an explainable rule (log-gap, device/SIM churn, burst-then-silence, "
        "odd-hour concentration); the triggering statistic is included in the description for evidentiary "
        "transparency.", STYLES["Small"]))


def _key_actor_section(elements, metrics):
    elements.append(Paragraph("Network Analysis — Key Actors", STYLES["H1"]))
    if not metrics.get("key_actors"):
        elements.append(Paragraph("Insufficient interaction data to compute network centrality.", STYLES["Body"]))
        return
    elements.append(Paragraph(
        f"The interaction network comprises <b>{metrics['nodes']}</b> entities and <b>{metrics['edges']}</b> "
        f"distinct relationships (graph density {metrics.get('density', 0)}). Entities are ranked below by a "
        f"composite of degree, betweenness and eigenvector centrality — standard social-network-analysis "
        f"measures of influence and brokerage. {_conf_tag('medium')}", STYLES["Body"]))
    data = [["Rank", "Entity", "Connections", "Interactions", "Degree C.", "Betweenness", "Eigenvector"]]
    for i, a in enumerate(metrics["key_actors"][:10], 1):
        data.append([str(i), Paragraph(str(a["entity"]), STYLES["Cell"]), str(a["degree"]),
                     str(a["total_interactions"]), f'{a["degree_centrality"]:.3f}',
                     f'{a["betweenness"]:.3f}', f'{a["eigenvector"]:.3f}'])
    elements.append(_styled_table(data, col_widths=[12 * mm, 42 * mm, 24 * mm, 24 * mm, 24 * mm, 24 * mm, 24 * mm]))
    if metrics.get("communities"):
        elements.append(Spacer(1, 6))
        elements.append(Paragraph("Community Detection (Modularity-Based Clusters)", STYLES["H2"]))
        for i, comm in enumerate(metrics["communities"][:5], 1):
            elements.append(Paragraph(f"<b>Cluster {i}</b> ({len(comm)} members): {', '.join(map(str, comm[:12]))}"
                                      + (" …" if len(comm) > 12 else ""), STYLES["Body"]))


def _custody_section(elements, logs):
    elements.append(PageBreak())
    elements.append(Paragraph("Appendix A — Chain of Custody", STYLES["H1"]))
    elements.append(Paragraph(
        "Complete, chronologically ordered log of evidentiary actions performed on this case. "
        "SHA-256 hashes were computed on raw file bytes prior to any parsing or transformation and may be "
        "used to independently verify source-file integrity.", STYLES["Body"]))
    if logs:
        data = [["Timestamp (UTC)", "Action", "File", "SHA-256 (truncated)", "Records", "Operator"]]
        for log in logs:
            data.append([
                log.upload_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                (log.action or "").upper(),
                Paragraph(log.file_name or "—", STYLES["Cell"]),
                Paragraph((log.sha256_hash[:24] + "…") if log.sha256_hash and log.sha256_hash != "N/A" else "—",
                          STYLES["Cell"]),
                str(log.record_count or 0),
                log.uploaded_by or "—",
            ])
        elements.append(_styled_table(data, col_widths=[32 * mm, 26 * mm, 38 * mm, 44 * mm, 16 * mm, 18 * mm]))
    else:
        elements.append(Paragraph("No custody entries recorded.", STYLES["Body"]))


def _sec65b_certificate(elements, case, summary, logs):
    """Section 63, Bharatiya Sakshya Adhiniyam 2023 (formerly s.65B, Indian
    Evidence Act 1872) certificate. Without this certificate, electronic
    records such as CDRs are not admissible in Indian courts
    (Anvar P.V. v. P.K. Basheer, (2014) 10 SCC 473; Arjun Panditrao
    Khotkar v. Kailash Kushanrao Gorantyal, (2020) 7 SCC 1)."""
    elements.append(PageBreak())
    cert = []
    cert.append(Paragraph(
        "Appendix B — Certificate under Section 63, Bharatiya Sakshya Adhiniyam, 2023",
        STYLES["H1"]))
    cert.append(Paragraph(
        "(Corresponding to Section 65B of the Indian Evidence Act, 1872 — certificate in respect of "
        "an electronic record.)", STYLES["Small"]))
    cert.append(Spacer(1, 6))

    files = ", ".join(sorted({l.file_name for l in logs if l.file_name})) or "the source CDR file(s) listed in Appendix A"
    hashes = "; ".join(sorted({
        f"{l.file_name}: {l.sha256_hash}" for l in logs
        if l.file_name and l.sha256_hash and l.sha256_hash != "N/A"
    })) or "as recorded in the chain-of-custody log at Appendix A"

    clauses = [
        ("1.", "I, the undersigned, hold a responsible official position in relation to the operation of the "
                "computer system / device by which the electronic record described in this report was produced "
                "and processed, and I am lawfully in charge of the said system for the purpose of this certificate."),
        ("2.", f"The electronic record annexed to and analysed in this report was produced from Call Detail "
                f"Records contained in the file(s): <b>{files}</b>. The said output was produced by the computer "
                f"during the period over which the computer was used regularly to store or process information "
                f"for the purposes of activities regularly carried on over that period."),
        ("3.", "Throughout the material part of the said period, the computer was operating properly; or if not, "
                "any respect in which it was not operating properly, or was out of operation during that part of "
                "the period, was not such as to affect the electronic record or the accuracy of its contents."),
        ("4.", "The information contained in the electronic record reproduces or is derived from information fed "
                "into the computer in the ordinary course of the said activities."),
        ("5.", f"The integrity of each source file was secured by computing its SHA-256 cryptographic hash on the "
                f"raw file bytes prior to any parsing or transformation. The recorded hash value(s) are: "
                f"<b>{hashes}</b>. Any alteration to a source file would change its hash and is therefore "
                f"independently detectable."),
        ("6.", "The contents of this report, including the derived analyses, are a true and faithful output of "
                "the CDR Analysis Platform operating on the said electronic records, to the best of my knowledge "
                "and belief."),
    ]
    for num, text in clauses:
        cert.append(Paragraph(f"<b>{num}</b>&nbsp;&nbsp;{text}", STYLES["CertBody"]))
        cert.append(Spacer(1, 3))

    case_name = case.case_name if case else "N/A"
    case_no = case.case_number if case else "N/A"
    cert.append(Spacer(1, 4))
    cert.append(Paragraph(
        f"This certificate is issued in respect of Case <b>{case_name}</b> (FIR/Case No. <b>{case_no}</b>) "
        f"and covers <b>{summary.get('cdr_count', 0)}</b> Call Detail Record(s) ingested into the platform.",
        STYLES["CertBody"]))
    cert.append(Spacer(1, 8 * mm))

    cert_sig = Table([
        [Paragraph("Signature: ______________________________", STYLES["CertBody"])],
        [Spacer(1, 4 * mm)],
        [Paragraph("Name: __________________________________", STYLES["CertBody"])],
        [Spacer(1, 4 * mm)],
        [Paragraph("Designation / Official position: __________________________________", STYLES["CertBody"])],
        [Spacer(1, 4 * mm)],
        [Paragraph("Organisation / Authority: __________________________________", STYLES["CertBody"])],
        [Spacer(1, 4 * mm)],
        [Paragraph("Place: ____________________________     Date: ____________________", STYLES["CertBody"])],
    ], colWidths=[174 * mm])
    cert_sig.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 1),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    cert.append(cert_sig)

    # Compulsorily keep the entire certificate on a single page.
    elements.append(KeepTogether(cert))


def _signature_block(elements):
    elements.append(Spacer(1, 18 * mm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=BORDER))
    elements.append(Spacer(1, 6 * mm))
    sig = Table([
        [Paragraph("_______________________________<br/><b>Analyst</b><br/>Name, designation &amp; signature",
                   STYLES["Small"]),
         Paragraph("_______________________________<br/><b>Reviewing Officer</b><br/>Name, designation &amp; signature",
                   STYLES["Small"]),
         Paragraph("_______________________________<br/><b>Date &amp; Place</b>", STYLES["Small"])],
    ], colWidths=[58 * mm, 58 * mm, 58 * mm])
    sig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("TOPPADDING", (0, 0), (-1, -1), 8)]))
    elements.append(sig)


def _fmt_time(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "N/A"


# ---------------------------------------------------------------------------
# Visuals (graphs & maps captured in the browser and posted as base64 PNGs)
# ---------------------------------------------------------------------------

# Max width available inside the page frame (A4 width - 2*18mm margins).
_MAX_IMG_W = 174 * mm
# Cap image height so a single visual never overruns one page.
_MAX_IMG_H = 150 * mm


def _decode_data_uri(data_uri):
    """Return a BytesIO of PNG bytes from a data URI or raw base64 string, or None."""
    if not data_uri or not isinstance(data_uri, str):
        return None
    payload = data_uri.strip()
    if payload.startswith("data:"):
        # data:image/png;base64,AAAA...
        comma = payload.find(",")
        if comma == -1:
            return None
        payload = payload[comma + 1:]
    try:
        raw = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError):
        return None
    if not raw:
        return None
    return BytesIO(raw)


def _scaled_image(bio):
    """Build a ReportLab Image scaled to fit the page frame, preserving aspect."""
    bio.seek(0)
    reader = ImageReader(bio)
    iw, ih = reader.getSize()
    if iw <= 0 or ih <= 0:
        return None
    ratio = ih / float(iw)
    w = _MAX_IMG_W
    h = w * ratio
    if h > _MAX_IMG_H:
        h = _MAX_IMG_H
        w = h / ratio
    bio.seek(0)
    return Image(bio, width=w, height=h)


def _visuals_section(elements, visuals):
    """Embed browser-captured graphs/maps. `visuals` is a list of
    {"title": str, "caption": str, "image": <data-uri>} dicts."""
    valid = []
    for v in (visuals or []):
        bio = _decode_data_uri(v.get("image"))
        if bio is None:
            continue
        img = _scaled_image(bio)
        if img is not None:
            valid.append((v.get("title", "Figure"), v.get("caption", ""), img))
    if not valid:
        return
    elements.append(PageBreak())
    elements.append(Paragraph("Visual Evidence — Graphs & Maps", STYLES["H1"]))
    elements.append(Paragraph(
        "The following figures were captured directly from the live analytical views of the "
        "CDR Analysis Platform for the records in scope. They are reproduced here as visual "
        f"aids to the tabular findings above. {_conf_tag('medium')}", STYLES["Body"]))
    elements.append(Spacer(1, 6))
    for i, (title, caption, img) in enumerate(valid, 1):
        block = [
            Paragraph(title, STYLES["H2"]),
            img,
            Paragraph(f"<b>Figure {i}.</b> {caption}" if caption else f"<b>Figure {i}.</b>", STYLES["Small"]),
            Spacer(1, 8),
        ]
        # Keep each figure with its caption; if it can't fit, start a fresh page.
        elements.append(KeepTogether(block))


# ---------------------------------------------------------------------------
# CDR report
# ---------------------------------------------------------------------------

def generate_pdf_report(db: Session, case_id: int, start_date: str = None, end_date: str = None,
                        sections: dict = None, visuals: list = None) -> BytesIO:
    """Build the forensic PDF.

    `sections` selects which parts to include (all default True):
        summary, key_actors, anomalies, records, visuals, custody, certificate
    `visuals` is a list of {"title","caption","image"(data-uri PNG)} captured
    from the browser graphs/maps.
    """
    sections = sections or {}

    def want(name):
        return sections.get(name, True)

    buffer = BytesIO()
    case = db.query(Case).filter(Case.id == case_id).first()
    case_label = f"Case: {case.case_number}" if case else f"Case ID {case_id}"
    doc = _make_doc(buffer, "CDR Investigation Report", case_label)

    summary = get_case_summary(db, case_id)
    cdrs = get_all_cdrs(db, case_id, start_date, end_date)
    anomalies = get_anomalies(db, case_id)
    metrics = get_graph_metrics(db, case_id, "cdr")
    logs = get_custody_log(db, case_id)

    elements = []
    _cover_page(elements, "CDR", case, summary)

    # Executive summary
    if want("summary"):
        elements.append(Paragraph("1. Executive Summary", STYLES["H1"]))
        scope = ""
        if start_date or end_date:
            scope = f" within the filter window {start_date or 'beginning'} to {end_date or 'present'}"
        high_sev = sum(1 for a in anomalies if a.severity == "high")
        top_actor = metrics["key_actors"][0]["entity"] if metrics.get("key_actors") else None
        summary_text = (
            f"This report covers <b>{len(cdrs)}</b> Call Detail Records{scope}, involving "
            f"<b>{summary.get('unique_entities', 0)}</b> unique entities across "
            f"<b>{summary.get('files_uploaded', 0)}</b> evidentiary file upload(s). "
            f"The anomaly engine raised <b>{len(anomalies)}</b> flag(s), of which <b>{high_sev}</b> are high severity. ")
        if top_actor:
            summary_text += (f"Network centrality analysis identifies <b>{top_actor}</b> as the most "
                             f"connected entity in the interaction graph. {_conf_tag('medium')}")
        elements.append(Paragraph(summary_text, STYLES["Body"]))
        elements.append(Spacer(1, 4))

        stats_pairs = [
            ("Total CDR records (case)", summary.get("cdr_count", 0)),
            ("Records in this report", len(cdrs)),
            ("Unique entities", summary.get("unique_entities", 0)),
            ("First event (UTC)", summary.get("first_event", "N/A")),
            ("Last event (UTC)", summary.get("last_event", "N/A")),
            ("Anomaly flags", len(anomalies)),
        ]
        elements.append(_kv_block(stats_pairs))
        elements.append(Spacer(1, 6))
        _methodology_note(elements)

    # Key actors
    if want("key_actors"):
        _key_actor_section(elements, metrics)
        elements.append(Spacer(1, 6))

    # Anomalies
    if want("anomalies"):
        _anomaly_section(elements, anomalies)

    # Record excerpt
    if want("records"):
        elements.append(PageBreak())
        elements.append(Paragraph("2. Call Detail Records (Excerpt)", STYLES["H1"]))
        if cdrs:
            shown = min(len(cdrs), 100)
            elements.append(Paragraph(
                f"Showing {shown} of {len(cdrs)} records, ordered chronologically (UTC-normalized). "
                f"The complete dataset is available via CSV export. {_conf_tag('high')}", STYLES["Body"]))
            data = [["#", "Time (UTC)", "Type", "Caller", "Callee", "Dur (s)", "Cell ID"]]
            for i, cdr in enumerate(cdrs[:100], 1):
                data.append([
                    str(i),
                    _fmt_time(cdr.normalized_time or cdr.start_time),
                    (cdr.event_type or "voice").upper(),
                    Paragraph(cdr.caller or "—", STYLES["Cell"]),
                    Paragraph(cdr.callee or "—", STYLES["Cell"]),
                    str(cdr.duration or 0),
                    cdr.cell_id or "—",
                ])
            elements.append(_styled_table(data, col_widths=[9 * mm, 34 * mm, 15 * mm, 34 * mm, 34 * mm, 14 * mm, 34 * mm]))
        else:
            elements.append(Paragraph("No CDR records available for the selected scope.", STYLES["Body"]))

    # Visual evidence — graphs & maps captured from the browser
    if want("visuals"):
        _visuals_section(elements, visuals)

    if want("custody"):
        _custody_section(elements, logs)
    if want("certificate"):
        _sec65b_certificate(elements, case, summary, logs)
    _signature_block(elements)

    doc.build(elements)

    log_action(db, case_id, "report_generated", file_name=f"case_{case_id}_cdr_report.pdf",
               record_count=len(cdrs))

    buffer.seek(0)
    return buffer


