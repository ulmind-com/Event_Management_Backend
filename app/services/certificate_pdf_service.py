import io
import qrcode
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle

class CertificatePDFService:
    @staticmethod
    def generate_qr_code_image(verification_url: str) -> io.BytesIO:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=4,
            border=2,
        )
        qr.add_data(verification_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='#1e293b', back_color='white')
        img_buffer = io.BytesIO()
        img.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        return img_buffer

    @staticmethod
    def generate_certificate_pdf(
        certificate_id: str,
        certificate_type: str,
        participant_name: str,
        activity_title: str,
        activity_type: str = 'COMPETITION',
        rank: int = None,
        award_title: str = None,
        issue_date_str: str = None,
        verification_url: str = None,
    ) -> bytes:
        buffer = io.BytesIO()
        width, height = landscape(A4)

        def draw_background(cv, doc):
            cv.saveState()
            cv.setStrokeColor(colors.HexColor('#0f172a'))
            cv.setLineWidth(4)
            cv.rect(18, 18, width - 36, height - 36)
            
            cv.setStrokeColor(colors.HexColor('#f59e0b'))
            cv.setLineWidth(1.5)
            cv.rect(24, 24, width - 48, height - 48)

            cv.setFillColor(colors.HexColor('#f59e0b'))
            cv.rect(22, 22, 10, 10, fill=1, stroke=0)
            cv.rect(width - 32, 22, 10, 10, fill=1, stroke=0)
            cv.rect(22, height - 32, 10, 10, fill=1, stroke=0)
            cv.rect(width - 32, height - 32, 10, 10, fill=1, stroke=0)
            cv.restoreState()

        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=50,
            rightMargin=50,
            topMargin=40,
            bottomMargin=35,
        )

        styles = getSampleStyleSheet()

        brand_style = ParagraphStyle(
            'BrandStyle',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=16,
            leading=20,
            alignment=1,
            textColor=colors.HexColor('#f59e0b'),
        )

        main_title = 'CERTIFICATE OF PARTICIPATION'
        if certificate_type == 'AWARD':
            main_title = 'CERTIFICATE OF EXCELLENCE'
        elif certificate_type == 'COURSE_COMPLETION':
            main_title = 'CERTIFICATE OF COURSE COMPLETION'
        elif certificate_type == 'WORKSHOP_COMPLETION':
            main_title = 'CERTIFICATE OF WORKSHOP COMPLETION'

        title_style = ParagraphStyle(
            'TitleStyle',
            parent=styles['Title'],
            fontName='Helvetica-Bold',
            fontSize=24,
            leading=28,
            alignment=1,
            textColor=colors.HexColor('#0f172a'),
        )

        subtitle_style = ParagraphStyle(
            'SubtitleStyle',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=12,
            leading=16,
            alignment=1,
            textColor=colors.HexColor('#64748b'),
        )

        name_style = ParagraphStyle(
            'NameStyle',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=22,
            leading=26,
            alignment=1,
            textColor=colors.HexColor('#1e3a8a'),
        )

        detail_style = ParagraphStyle(
            'DetailStyle',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=12,
            leading=18,
            alignment=1,
            textColor=colors.HexColor('#334155'),
        )

        award_style = ParagraphStyle(
            'AwardStyle',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=15,
            leading=20,
            alignment=1,
            textColor=colors.HexColor('#d97706'),
        )

        meta_style = ParagraphStyle(
            'MetaStyle',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=8,
            leading=11,
            textColor=colors.HexColor('#475569'),
        )

        story = []
        story.append(Spacer(1, 10))
        story.append(Paragraph('EVENTORA LEARNING & COMPETITIONS', brand_style))
        story.append(Spacer(1, 10))
        story.append(Paragraph(main_title, title_style))
        story.append(Spacer(1, 12))
        story.append(Paragraph('PROUDLY PRESENTED TO', subtitle_style))
        story.append(Spacer(1, 10))
        story.append(Paragraph(participant_name, name_style))
        story.append(Spacer(1, 10))

        if certificate_type == 'AWARD' and rank:
            rank_text = f'Rank {rank}'
            if award_title:
                rank_text += f' &mdash; {award_title}'
            story.append(Paragraph(f'In recognition of outstanding performance achieving <b>{rank_text}</b> in', detail_style))
            story.append(Paragraph(f'<b>{activity_title}</b>', award_style))
        else:
            story.append(Paragraph(f'for successful participation in <b>{activity_title}</b>', detail_style))

        story.append(Spacer(1, 16))

        v_url = verification_url if verification_url else f'https://eventora.com/verify-certificate/{certificate_id}'
        qr_buf = CertificatePDFService.generate_qr_code_image(v_url)
        qr_img = Image(qr_buf, width=70, height=70)

        issue_display = issue_date_str if issue_date_str else 'N/A'
        left_text = f'<b>Certificate ID:</b> {certificate_id}<br/><b>Issued Date:</b> {issue_display}<br/><b>Status:</b> Official & Verified<br/><b>Scan QR to verify authenticity</b>'
        right_text = '<br/><br/>___________________________<br/><b>Eventora Authorised Signatory</b><br/>Director of Competitions'

        footer_table = Table(
            [
                [Paragraph(left_text, meta_style), qr_img, Paragraph(right_text, ParagraphStyle('SignStyle', parent=meta_style, alignment=2))]
            ],
            colWidths=[240, 100, 320]
        )
        footer_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN', (1,0), (1,0), 'CENTER'),
            ('ALIGN', (2,0), (2,0), 'RIGHT'),
        ]))

        story.append(footer_table)

        doc.build(story, onFirstPage=draw_background)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes
