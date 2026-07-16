from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from services.ingestion import get_all_cdrs

def generate_pdf_report(start_date: str = None, end_date: str = None) -> BytesIO:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    
    styles = getSampleStyleSheet()
    title = Paragraph("CDR Investigation Report", styles['Title'])
    elements.append(title)
    elements.append(Spacer(1, 12))
    
    cdrs = get_all_cdrs(start_date, end_date)
    
    summary = Paragraph(f"Total Ingested CDRs: {len(cdrs)}", styles['Normal'])
    elements.append(summary)
    elements.append(Spacer(1, 12))
    
    if cdrs:
        data = [["Caller", "Callee", "Start Time", "Duration (s)", "Cell ID"]]
        # Limit to first 50 for the report table to avoid massive PDFs
        for cdr in cdrs[:50]:
            data.append([
                cdr.caller,
                cdr.callee,
                cdr.start_time.strftime("%Y-%m-%d %H:%M:%S") if cdr.start_time else "N/A",
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
    
    doc.build(elements)
    buffer.seek(0)
    return buffer

def generate_ipdr_pdf_report(start_date: str = None, end_date: str = None) -> BytesIO:
    from services.ingestion import get_all_ipdrs
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    
    styles = getSampleStyleSheet()
    title = Paragraph("IPDR Investigation Report", styles['Title'])
    elements.append(title)
    elements.append(Spacer(1, 12))
    
    ipdrs = get_all_ipdrs(start_date, end_date)
    
    summary = Paragraph(f"Total Ingested IPDRs: {len(ipdrs)}", styles['Normal'])
    elements.append(summary)
    elements.append(Spacer(1, 12))
    
    if ipdrs:
        data = [["Source IP", "Dest IP", "Start Time", "Duration (s)", "Protocol"]]
        for ipdr in ipdrs[:50]:
            dur = (ipdr.session_end - ipdr.session_start).total_seconds() if ipdr.session_end and ipdr.session_start else 0
            data.append([
                ipdr.source_ip,
                ipdr.dest_ip,
                ipdr.session_start.strftime("%Y-%m-%d %H:%M:%S") if ipdr.session_start else "N/A",
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
    
    doc.build(elements)
    buffer.seek(0)
    return buffer
