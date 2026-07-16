from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from sqlalchemy.orm import Session

from services.ingestion import get_all_cdrs, get_all_ipdrs
from services.analysis import get_custody_log, get_anomalies

def generate_pdf_report(db: Session, case_id: int, start_date: str = None, end_date: str = None) -> BytesIO:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    
    styles = getSampleStyleSheet()
    title = Paragraph(f"CDR Investigation Report - Case {case_id}", styles['Title'])
    elements.append(title)
    elements.append(Spacer(1, 12))
    
    cdrs = get_all_cdrs(db, case_id, start_date, end_date)
    
    summary = Paragraph(f"Total Ingested CDRs: {len(cdrs)}", styles['Normal'])
    elements.append(summary)
    elements.append(Spacer(1, 12))
    
    if cdrs:
        data = [["Caller", "Callee", "Start Time", "Duration (s)", "Cell ID"]]
        for cdr in cdrs[:50]:
            dt_str = cdr.normalized_time.strftime("%Y-%m-%d %H:%M:%S") if cdr.normalized_time else (cdr.start_time.strftime("%Y-%m-%d %H:%M:%S") if cdr.start_time else "N/A")
            data.append([
                cdr.caller,
                cdr.callee,
                dt_str,
                str(cdr.duration),
                str(cdr.cell_id) if cdr.cell_id else "N/A"
            ])
            
        table = Table(data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
        ]))
        elements.append(table)
    else:
        elements.append(Paragraph("No data available to report.", styles['Normal']))
        
    elements.append(Spacer(1, 24))
    
    # Anomalies Section
    anomalies = get_anomalies(db, case_id)
    if anomalies:
        elements.append(Paragraph("Detected Anomalies", styles['Heading2']))
        for a in anomalies:
            conf_badge = f" [Confidence: {a.confidence.upper() if a.confidence else 'N/A'}]"
            elements.append(Paragraph(f"• Subject {a.subject_id} - {a.flag_type}: {a.description}{conf_badge}", styles['Normal']))
        elements.append(Spacer(1, 24))
    
    # Chain of Custody Section
    elements.append(Paragraph("Chain of Custody (Appendix)", styles['Heading2']))
    logs = get_custody_log(db, case_id)
    if logs:
        log_data = [["Timestamp", "Action", "File Name", "Hash", "User"]]
        for log in logs:
            log_data.append([
                log.upload_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                log.action,
                log.file_name,
                log.sha256_hash[:16] + "..." if log.sha256_hash else "",
                log.uploaded_by
            ])
        log_table = Table(log_data)
        log_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ]))
        elements.append(log_table)
    else:
        elements.append(Paragraph("No custody logs found.", styles['Normal']))
    
    doc.build(elements)
    buffer.seek(0)
    return buffer

def generate_ipdr_pdf_report(db: Session, case_id: int, start_date: str = None, end_date: str = None) -> BytesIO:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    
    styles = getSampleStyleSheet()
    title = Paragraph(f"IPDR Investigation Report - Case {case_id}", styles['Title'])
    elements.append(title)
    elements.append(Spacer(1, 12))
    
    ipdrs = get_all_ipdrs(db, case_id, start_date, end_date)
    
    summary = Paragraph(f"Total Ingested IPDRs: {len(ipdrs)}", styles['Normal'])
    elements.append(summary)
    elements.append(Spacer(1, 12))
    
    if ipdrs:
        data = [["Source IP", "Dest IP", "Start Time", "Duration (s)", "Protocol"]]
        for ipdr in ipdrs[:50]:
            dur = (ipdr.session_end - ipdr.session_start).total_seconds() if ipdr.session_end and ipdr.session_start else 0
            dt_str = ipdr.normalized_session_start.strftime("%Y-%m-%d %H:%M:%S") if ipdr.normalized_session_start else (ipdr.session_start.strftime("%Y-%m-%d %H:%M:%S") if ipdr.session_start else "N/A")
            data.append([
                ipdr.source_ip,
                ipdr.dest_ip,
                dt_str,
                str(int(dur)),
                str(ipdr.protocol)
            ])
            
        table = Table(data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
        ]))
        elements.append(table)
    else:
        elements.append(Paragraph("No data available to report.", styles['Normal']))
        
    elements.append(Spacer(1, 24))
    
    # Chain of Custody Section
    elements.append(Paragraph("Chain of Custody (Appendix)", styles['Heading2']))
    logs = get_custody_log(db, case_id)
    if logs:
        log_data = [["Timestamp", "Action", "File Name", "Hash", "User"]]
        for log in logs:
            log_data.append([
                log.upload_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                log.action,
                log.file_name,
                log.sha256_hash[:16] + "..." if log.sha256_hash else "",
                log.uploaded_by
            ])
        log_table = Table(log_data)
        log_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ]))
        elements.append(log_table)
    else:
        elements.append(Paragraph("No custody logs found.", styles['Normal']))
    
    doc.build(elements)
    buffer.seek(0)
    return buffer
