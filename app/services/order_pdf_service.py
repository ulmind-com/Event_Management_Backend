import io
import qrcode
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, HRFlowable


class OrderPDFService:
    """
    Production-grade Order Invoice and Receipt PDF Generator.
    Generates professional, authoritative PDF receipts for merchandise purchases.
    """

    @staticmethod
    def generate_qr_code(url: str, size: int = 65) -> io.BytesIO:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=3,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0f172a", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    @staticmethod
    def format_currency(amount: Any, currency: str = "INR") -> str:
        try:
            val = float(amount or 0.0)
            return f"Rs. {val:,.2f}"
        except (ValueError, TypeError):
            return "Rs. 0.00"

    @classmethod
    def generate_order_invoice_pdf(cls, order: Dict[str, Any]) -> bytes:
        buffer = io.BytesIO()

        # Usable width: 595.27 - 72 = 523.27 pt
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        styles = getSampleStyleSheet()

        # Typography tokens
        brand_style = ParagraphStyle(
            'InvoiceBrand',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=15,
            leading=18,
            textColor=colors.HexColor('#0f172a'),
        )
        brand_sub = ParagraphStyle(
            'InvoiceBrandSub',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=8,
            leading=11,
            textColor=colors.HexColor('#64748b'),
        )
        header_title = ParagraphStyle(
            'InvoiceTitle',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=16,
            leading=19,
            alignment=2,
            textColor=colors.HexColor('#0284c7'),
        )
        header_meta = ParagraphStyle(
            'InvoiceMeta',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=8.5,
            leading=12,
            alignment=2,
            textColor=colors.HexColor('#334155'),
        )
        section_heading = ParagraphStyle(
            'SectionHead',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=9.5,
            leading=12,
            textColor=colors.HexColor('#0f172a'),
        )
        body_text = ParagraphStyle(
            'BodyTxt',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=8.5,
            leading=12,
            textColor=colors.HexColor('#334155'),
        )
        table_head = ParagraphStyle(
            'TableHead',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=8.5,
            leading=11,
            textColor=colors.white,
        )
        table_cell = ParagraphStyle(
            'TableCell',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=8,
            leading=11,
            textColor=colors.HexColor('#1e293b'),
        )
        table_cell_bold = ParagraphStyle(
            'TableCellBold',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=8,
            leading=11,
            textColor=colors.HexColor('#0f172a'),
        )
        table_cell_right = ParagraphStyle(
            'TableCellRight',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=8,
            leading=11,
            alignment=2,
            textColor=colors.HexColor('#1e293b'),
        )
        table_cell_right_bold = ParagraphStyle(
            'TableCellRightBold',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=8.5,
            leading=11,
            alignment=2,
            textColor=colors.HexColor('#0f172a'),
        )
        policy_notice = ParagraphStyle(
            'PolicyNotice',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor('#64748b'),
        )

        elements = []

        # ── 1. Top Header Banner ──
        order_number = order.get("order_number", "N/A")
        raw_created_at = order.get("created_at")
        if isinstance(raw_created_at, datetime):
            created_str = raw_created_at.strftime("%d %b %Y, %I:%M %p")
        elif raw_created_at:
            created_str = str(raw_created_at)[:19]
        else:
            created_str = datetime.now(timezone.utc).strftime("%d %b %Y")

        payment_status = (order.get("payment_status") or "PAID").upper()
        order_status = (order.get("status") or "CONFIRMED").upper()

        status_color = "#16a34a" if payment_status == "PAID" else "#d97706"

        left_brand = [
            Paragraph("UPGRADE TALENTS & EVENTORA", brand_style),
            Paragraph("Official Merchandise & Apparel Division", brand_sub),
            Paragraph("Registered Office: Kolkata, West Bengal, India", brand_sub),
            Paragraph("Support: support@eventora.com | www.eventora.com", brand_sub),
        ]
        right_meta = [
            Paragraph("TAX INVOICE / ORDER RECEIPT", header_title),
            Spacer(1, 3),
            Paragraph(f"<b>Order No:</b> {order_number}", header_meta),
            Paragraph(f"<b>Order Date:</b> {created_str}", header_meta),
            Paragraph(f"<b>Payment:</b> <font color='{status_color}'><b>{payment_status}</b></font>", header_meta),
            Paragraph(f"<b>Fulfillment:</b> <b>{order_status}</b>", header_meta),
        ]

        header_table = Table([[left_brand, right_meta]], colWidths=[290, 233])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 6))

        # Horizontal Divider
        elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#0284c7"), spaceAfter=10))

        # ── 2. Customer & Delivery Address vs Payment Summary ──
        ship = order.get("shipping_address") or {}
        cust_name = ship.get("full_name") or order.get("user_name") or "Valued Customer"
        cust_phone = ship.get("phone") or "N/A"
        cust_email = order.get("user_email") or "N/A"
        addr_parts = [
            ship.get("address_line1"),
            ship.get("address_line2"),
            f"{ship.get('city', '')}, {ship.get('state', '')} {ship.get('postal_code', '')}".strip(" ,"),
            ship.get("country", "India")
        ]
        cust_address = "<br/>".join([p for p in addr_parts if p]) or "Delivery address not provided."

        left_cust = [
            Paragraph("BILLED & SHIPPED TO", section_heading),
            Spacer(1, 3),
            Paragraph(f"<b>{cust_name}</b>", body_text),
            Paragraph(f"<b>Phone:</b> {cust_phone}", body_text),
            Paragraph(f"<b>Email:</b> {cust_email}", body_text),
            Paragraph(f"<b>Address:</b><br/>{cust_address}", body_text),
        ]

        pay_ref = order.get("payment_reference") or order.get("provider_order_id") or "Direct Gateway Verified"
        courier = order.get("courier_name") or "Standard Courier Logistics"
        tracking = order.get("tracking_number") or "Assigned on dispatch"

        right_pay = [
            Paragraph("PAYMENT & DISPATCH DETAILS", section_heading),
            Spacer(1, 3),
            Paragraph("<b>Payment Gateway:</b> Razorpay Online Payments", body_text),
            Paragraph(f"<b>Payment Txn ID:</b> {pay_ref}", body_text),
            Paragraph(f"<b>Courier Partner:</b> {courier}", body_text),
            Paragraph(f"<b>AWB Tracking No:</b> {tracking}", body_text),
            Paragraph("<b>Policy:</b> Strictly Non-Refundable / Final Sale", body_text),
        ]

        info_table = Table([[left_cust, right_pay]], colWidths=[261, 262])
        info_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BACKGROUND', (0, 0), (0, 0), colors.HexColor("#f8fafc")),
            ('BACKGROUND', (1, 0), (1, 0), colors.HexColor("#f8fafc")),
            ('BOX', (0, 0), (0, 0), 0.5, colors.HexColor("#cbd5e1")),
            ('BOX', (1, 0), (1, 0), 0.5, colors.HexColor("#cbd5e1")),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 14))

        # ── 3. Ordered Products Table ──
        items = order.get("items") or []
        currency = order.get("currency", "INR")

        table_data = [
            [
                Paragraph("#", table_head),
                Paragraph("Item Description", table_head),
                Paragraph("Variant / SKU", table_head),
                Paragraph("Qty", table_head),
                Paragraph("Unit Price", ParagraphStyle('THRight', parent=table_head, alignment=2)),
                Paragraph("Total", ParagraphStyle('THRight', parent=table_head, alignment=2)),
            ]
        ]

        for idx, it in enumerate(items, 1):
            p_name = it.get("product_name") or it.get("title") or it.get("name") or "Official Merchandise Item"
            variant = it.get("variant") or {}
            v_parts = [variant.get("color"), variant.get("size")]
            v_text = " · ".join([p for p in v_parts if p])
            sku = it.get("sku") or variant.get("sku") or ""
            var_desc = f"{v_text}<br/>SKU: {sku}" if sku else (v_text or "Standard")

            qty = int(it.get("quantity") or 1)
            unit_p = float(it.get("unit_price") or it.get("price") or 0.0)
            line_tot = float(it.get("total_price") or (unit_p * qty))

            table_data.append([
                Paragraph(str(idx), table_cell),
                Paragraph(f"<b>{p_name}</b>", table_cell),
                Paragraph(var_desc, table_cell),
                Paragraph(str(qty), table_cell),
                Paragraph(cls.format_currency(unit_p, currency), table_cell_right),
                Paragraph(cls.format_currency(line_tot, currency), table_cell_right_bold),
            ])

        if not items:
            table_data.append([
                Paragraph("1", table_cell),
                Paragraph("<b>Official Event Merchandise Order</b>", table_cell),
                Paragraph("Standard", table_cell),
                Paragraph("1", table_cell),
                Paragraph(cls.format_currency(order.get("total_amount", 0.0), currency), table_cell_right),
                Paragraph(cls.format_currency(order.get("total_amount", 0.0), currency), table_cell_right_bold),
            ])

        # Widths total 523pt: 25 + 238 + 90 + 35 + 65 + 70 = 523pt
        items_table = Table(table_data, colWidths=[25, 238, 90, 35, 65, 70])
        items_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0f172a")),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ]))
        elements.append(items_table)
        elements.append(Spacer(1, 10))

        # ── 4. Financial Summary Calculation Table ──
        subtotal = float(order.get("subtotal") or order.get("total_amount") or 0.0)
        shipping_fee = float(order.get("shipping_fee") or 0.0)
        discount = float(order.get("discount_amount") or 0.0)
        grand_total = float(order.get("total_amount") or (subtotal + shipping_fee - discount))

        summary_rows = [
            ["", "", Paragraph("Subtotal:", table_cell_right), Paragraph(cls.format_currency(subtotal, currency), table_cell_right)],
            ["", "", Paragraph("Shipping & Handling:", table_cell_right), Paragraph("FREE" if shipping_fee <= 0 else cls.format_currency(shipping_fee, currency), table_cell_right)],
        ]
        if discount > 0:
            summary_rows.append([
                "", "", Paragraph("Discount Applied:", table_cell_right), Paragraph(f"-{cls.format_currency(discount, currency)}", table_cell_right)
            ])
        summary_rows.append([
            "", "", Paragraph("<b>TOTAL AMOUNT PAID:</b>", ParagraphStyle('TotL', parent=table_cell_right, fontSize=9.5, textColor=colors.HexColor("#0f172a"))),
            Paragraph(f"<b>{cls.format_currency(grand_total, currency)}</b>", ParagraphStyle('TotR', parent=table_cell_right_bold, fontSize=10, textColor=colors.HexColor("#0284c7")))
        ])

        summary_table = Table(summary_rows, colWidths=[200, 153, 100, 70])
        summary_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (2, 0), (-1, -1), 4),
            ('RIGHTPADDING', (2, 0), (-1, -1), 4),
            ('TOPPADDING', (2, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (2, 0), (-1, -1), 3),
            ('LINEABOVE', (2, -1), (3, -1), 1, colors.HexColor("#0f172a")),
            ('LINEBELOW', (2, -1), (3, -1), 1.5, colors.HexColor("#0f172a")),
        ]))
        elements.append(summary_table)
        elements.append(Spacer(1, 16))

        # ── 5. Terms & Policy Box ──
        policy_box = Table(
            [[
                Paragraph(
                    "<b>TERMS & MERCHANDISE POLICY:</b><br/>"
                    "1. Official Merchandise Sales are strictly final and non-refundable once confirmed.<br/>"
                    "2. All items undergo strict multi-stage quality control before dispatch and handover to courier.<br/>"
                    "3. For damaged transit packaging or replacement requests due to transit damage, contact <b>support@eventora.com</b> with order number within 48 hours of delivery.<br/>"
                    "4. This invoice serves as proof of purchase and authenticity.",
                    policy_notice
                )
            ]],
            colWidths=[523]
        )
        policy_box.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(policy_box)
        elements.append(Spacer(1, 14))

        # ── 6. Bottom Signature & Verification QR Code ──
        track_url = f"https://eventora.com/merchandise/orders/{order_number}"
        qr_buf = cls.generate_qr_code(track_url)
        qr_img = Image(qr_buf, width=60, height=60)

        left_sign = [
            Paragraph("<b>Scan QR to Track Live Shipment:</b>", ParagraphStyle('QRTxt', parent=body_text, fontSize=7.5)),
            Paragraph(f"<font color='#0284c7'><u>{track_url}</u></font>", ParagraphStyle('QRTxt2', parent=body_text, fontSize=7)),
            Spacer(1, 4),
            Paragraph("Thank you for choosing Upgrade Talents official gear!", ParagraphStyle('QRTxt3', parent=body_text, fontSize=8, fontName='Helvetica-Oblique')),
        ]
        right_sign = [
            Paragraph("For <b>UPGRADE TALENTS INDIA PVT LTD</b>", ParagraphStyle('SignHead', parent=body_text, alignment=2, fontSize=8)),
            Spacer(1, 24),
            Paragraph("<b>Authorised Signatory</b>", ParagraphStyle('SignBtm', parent=body_text, alignment=2, fontSize=8.5)),
            Paragraph("Official Store Logistics & Fulfillment", ParagraphStyle('SignSub', parent=body_text, alignment=2, fontSize=7.5, textColor=colors.HexColor("#64748b"))),
        ]

        footer_table = Table([[qr_img, left_sign, right_sign]], colWidths=[65, 278, 180])
        footer_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        elements.append(footer_table)

        doc.build(elements)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes
