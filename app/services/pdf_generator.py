from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch

class PDFGenerator:
    @staticmethod
    def generate_participants_pdf(competition_title: str, participants: list) -> bytes:
        """
        Generates a PDF containing a table of participants.
        Returns the PDF as raw bytes.
        """
        buffer = BytesIO()
        # Landscape A4 for wider tables
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=18)
        elements = []
        
        styles = getSampleStyleSheet()
        
        # Title
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            spaceAfter=14,
            textColor=colors.HexColor("#2C3E50")
        )
        
        elements.append(Paragraph(f"Registered Participants Report", title_style))
        elements.append(Paragraph(f"Competition: {competition_title}", styles["Normal"]))
        elements.append(Spacer(1, 0.25 * inch))
        
        # Table Data
        # Columns: SL No, Name, Email, Phone, Status
        data = [["SL No.", "Participant Name", "Email Address", "Phone Number", "Status"]]
        
        for idx, p in enumerate(participants, 1):
            data.append([
                str(idx),
                p.get("name", "N/A"),
                p.get("email", "N/A"),
                str(p.get("phone", "N/A")),
                p.get("status", "N/A")
            ])
            
        # Create Table
        # Column widths: 50, 150, 200, 150, 100
        t = Table(data, colWidths=[50, 180, 220, 120, 100])
        
        # Add Style
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#34495E")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'), # Center SL No
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('TOPPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor("#F9F9F9")),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor("#DDDDDD")),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6F9")])
        ]))
        
        elements.append(t)
        doc.build(elements)
        
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes

    @staticmethod
    def generate_course_registrations_pdf(comp: dict, docs: list) -> bytes:
        from io import BytesIO
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        import datetime
        
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=18)
        elements = []
        styles = getSampleStyleSheet()
        
        title_text = f"Registration Report: {comp.get('title', 'Unknown')} ({comp.get('course_type', 'Course').capitalize()})"
        elements.append(Paragraph(title_text, styles['Heading1']))
        elements.append(Spacer(1, 10))
        
        meta_text = f"Registration Window: {comp.get('registration_start_at')} to {comp.get('registration_end_at')} | Total Registered: {len(docs)}"
        elements.append(Paragraph(meta_text, styles['Normal']))
        elements.append(Spacer(1, 20))
        
        data = [["SL", "Reg ID", "Name", "Email", "Date", "Status"]]
        
        for idx, r in enumerate(docs, start=1):
            date_str = r.get("created_at", datetime.datetime.now()).strftime("%Y-%m-%d %H:%M") if hasattr(r.get("created_at"), "strftime") else str(r.get("created_at"))
            data.append([
                str(idx),
                str(r.get("_id", "")),
                str(r.get("participant_name", "N/A")),
                str(r.get("participant_email", "N/A")),
                date_str,
                str(r.get("status", "N/A"))
            ])
            
        if len(data) == 1:
            data.append(["-", "-", "No registrations found", "-", "-", "-"])
            
        table = Table(data, colWidths=[40, 150, 150, 200, 100, 100])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.grey),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 10),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
            ('BACKGROUND', (0,1), (-1,-1), colors.beige),
            ('GRID', (0,0), (-1,-1), 1, colors.black),
            ('FONTSIZE', (0,1), (-1,-1), 8),
        ]))
        
        elements.append(table)
        doc.build(elements)
        
        return buffer.getvalue()
