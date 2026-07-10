"""
PDF and DOCX generation utilities for SOW documents.
Uses reportlab for PDF and python-docx for DOCX generation.
"""
from typing import Dict, Any
from io import BytesIO
import logging

logger = logging.getLogger(__name__)


def generate_sow_pdf(sow: Dict[str, Any]) -> BytesIO:
    """
    Generate a professional PDF from SOW data.
    Returns BytesIO buffer that can be sent as download or email attachment.
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
    except ImportError:
        logger.error("reportlab not installed. Install with: pip install reportlab")
        raise ImportError("reportlab is required for PDF generation")
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.75*inch, bottomMargin=0.75*inch)
    story = []
    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Title'],
        fontSize=24,
        textColor=colors.HexColor('#1e40af'),
        spaceAfter=12,
        alignment=TA_CENTER
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading1'],
        fontSize=16,
        textColor=colors.HexColor('#1e40af'),
        spaceAfter=12,
        spaceBefore=12
    )
    
    subheading_style = ParagraphStyle(
        'CustomSubheading',
        parent=styles['Heading2'],
        fontSize=13,
        textColor=colors.HexColor('#475569'),
        spaceAfter=8,
        spaceBefore=8
    )
    
    body_style = ParagraphStyle(
        'CustomBody',
        parent=styles['BodyText'],
        fontSize=11,
        alignment=TA_JUSTIFY,
        spaceAfter=8
    )
    
    # Title Page
    story.append(Paragraph("STATEMENT OF WORK", title_style))
    story.append(Spacer(1, 0.2*inch))
    
    # Metadata
    metadata = sow.get('metadata', {})
    meta_text = f"<b>Generated:</b> {metadata.get('generated_at', 'N/A')}<br/>"
    meta_text += f"<b>Version:</b> {metadata.get('version', '1.0')}<br/>"
    meta_text += f"<b>Job ID:</b> {metadata.get('job_id', 'N/A')}"
    story.append(Paragraph(meta_text, body_style))
    story.append(Spacer(1, 0.3*inch))
    
    # Executive Summary
    exec_summary = sow.get('executive_summary', {})
    story.append(Paragraph(exec_summary.get('title', 'Executive Summary'), heading_style))
    story.append(Paragraph(exec_summary.get('content', ''), body_style))
    story.append(Spacer(1, 0.2*inch))
    
    # Site Information
    site_info = sow.get('site_information', {})
    story.append(Paragraph(site_info.get('title', 'Site Information'), heading_style))
    
    site_data = [
        ['Account:', site_info.get('account_name', 'N/A')],
        ['Address:', site_info.get('address', 'N/A')],
    ]
    contact = site_info.get('primary_contact', {})
    site_data.append(['Contact Name:', contact.get('name', 'N/A')])
    site_data.append(['Contact Email:', contact.get('email', 'N/A')])
    site_data.append(['Contact Phone:', contact.get('phone', 'N/A')])
    
    site_table = Table(site_data, colWidths=[2*inch, 4.5*inch])
    site_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f1f5f9')),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey)
    ]))
    story.append(site_table)
    story.append(Spacer(1, 0.2*inch))
    
    # Current State Assessment
    current_state = sow.get('current_state_assessment', {})
    story.append(Paragraph(current_state.get('title', 'Current State Assessment'), heading_style))
    
    for finding in current_state.get('findings', []):
        story.append(Paragraph(finding.get('category', ''), subheading_style))
        finding_text = f"<b>{finding.get('item', '')}</b>"
        if finding.get('details'):
            finding_text += f"<br/>{finding.get('details')}"
        story.append(Paragraph(finding_text, body_style))
    
    story.append(Spacer(1, 0.2*inch))
    
    # Scope of Work
    scope = sow.get('scope_of_work', {})
    story.append(Paragraph(scope.get('title', 'Scope of Work'), heading_style))
    story.append(Paragraph(scope.get('description', ''), body_style))
    
    story.append(Paragraph("Deliverables:", subheading_style))
    for deliverable in scope.get('deliverables', []):
        story.append(Paragraph(f"• {deliverable}", body_style))
    
    story.append(Paragraph("Tasks:", subheading_style))
    for task in scope.get('tasks', []):
        task_text = f"• <b>{task.get('task')}</b> (Qty: {task.get('quantity')}): {task.get('description')}"
        story.append(Paragraph(task_text, body_style))
    
    story.append(Spacer(1, 0.2*inch))
    
    # Materials and Equipment
    materials = sow.get('materials_and_equipment', {})
    story.append(Paragraph(materials.get('title', 'Materials and Equipment'), heading_style))
    
    story.append(Paragraph("Materials Required:", subheading_style))
    mat_data = [['Item', 'Quantity']]
    for mat in materials.get('materials', []):
        mat_data.append([mat.get('item', ''), mat.get('quantity', 'TBD')])
    
    if len(mat_data) > 1:
        mat_table = Table(mat_data, colWidths=[4*inch, 2.5*inch])
        mat_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')])
        ]))
        story.append(mat_table)
    
    story.append(Spacer(1, 0.1*inch))
    story.append(Paragraph("Tools Required:", subheading_style))
    for tool in materials.get('tools', []):
        story.append(Paragraph(f"• {tool}", body_style))
    
    story.append(Spacer(1, 0.2*inch))
    
    # Timeline
    timeline = sow.get('timeline_estimate', {})
    story.append(Paragraph(timeline.get('title', 'Timeline Estimate'), heading_style))
    timeline_text = f"<b>Estimated Hours:</b> {timeline.get('estimated_hours', 0)}<br/>"
    timeline_text += f"<b>Estimated Days:</b> {timeline.get('estimated_days', 0)}<br/>"
    timeline_text += f"<b>Technicians Required:</b> {timeline.get('technicians_required', 1)}"
    story.append(Paragraph(timeline_text, body_style))
    story.append(Spacer(1, 0.2*inch))
    
    # Cost Estimate
    cost = sow.get('cost_estimate', {})
    story.append(Paragraph(cost.get('title', 'Cost Estimate'), heading_style))
    
    labor = cost.get('labor', {})
    materials_cost = cost.get('materials', {})
    
    cost_data = [
        ['Description', 'Details', 'Amount'],
        ['Labor', f"{labor.get('hours', 0)} hrs @ ${labor.get('rate', 0):.2f}/hr", f"${labor.get('subtotal', 0):.2f}"],
        ['Materials', 'Estimated materials cost', f"${materials_cost.get('estimated_cost', 0):.2f}"],
        ['', '<b>TOTAL ESTIMATE</b>', f"<b>${cost.get('total', 0):.2f}</b>"]
    ]
    
    cost_table = Table(cost_data, colWidths=[2*inch, 2.5*inch, 2*inch])
    cost_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f1f5f9')),
        ('FONTNAME', (1, -1), (-1, -1), 'Helvetica-Bold')
    ]))
    story.append(cost_table)
    story.append(Spacer(1, 0.1*inch))
    story.append(Paragraph(f"<i>{cost.get('notes', '')}</i>", body_style))
    
    # Page Break before Terms
    story.append(PageBreak())
    
    # Terms and Conditions
    terms = sow.get('terms_and_conditions', {})
    story.append(Paragraph(terms.get('title', 'Terms and Conditions'), heading_style))
    
    for section in terms.get('sections', []):
        story.append(Paragraph(section.get('heading', ''), subheading_style))
        story.append(Paragraph(section.get('content', ''), body_style))
    
    # Footer note
    story.append(Spacer(1, 0.3*inch))
    footer_text = "<i>This document is a preliminary statement of work based on site survey findings. Final scope and costs will be confirmed upon project commencement.</i>"
    story.append(Paragraph(footer_text, body_style))
    
    # Build PDF
    doc.build(story)
    buffer.seek(0)
    return buffer


def generate_sow_docx(sow: Dict[str, Any]) -> BytesIO:
    """
    Generate a professional DOCX from SOW data.
    Returns BytesIO buffer that can be sent as download or email attachment.
    """
    try:
        from docx import Document
        from docx.shared import Inches, Pt, RGBColor
        from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    except ImportError:
        logger.error("python-docx not installed. Install with: pip install python-docx")
        raise ImportError("python-docx is required for DOCX generation")
    
    doc = Document()
    
    # Title
    title = doc.add_heading('STATEMENT OF WORK', 0)
    title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    
    # Metadata
    metadata = sow.get('metadata', {})
    meta_p = doc.add_paragraph()
    meta_p.add_run('Generated: ').bold = True
    meta_p.add_run(f"{metadata.get('generated_at', 'N/A')}\n")
    meta_p.add_run('Version: ').bold = True
    meta_p.add_run(f"{metadata.get('version', '1.0')}\n")
    meta_p.add_run('Job ID: ').bold = True
    meta_p.add_run(f"{metadata.get('job_id', 'N/A')}")
    
    doc.add_paragraph()  # Spacer
    
    # Executive Summary
    exec_summary = sow.get('executive_summary', {})
    doc.add_heading(exec_summary.get('title', 'Executive Summary'), 1)
    doc.add_paragraph(exec_summary.get('content', ''))
    
    # Site Information
    site_info = sow.get('site_information', {})
    doc.add_heading(site_info.get('title', 'Site Information'), 1)
    
    site_table = doc.add_table(rows=5, cols=2)
    site_table.style = 'Light Grid Accent 1'
    site_table.rows[0].cells[0].text = 'Account:'
    site_table.rows[0].cells[1].text = site_info.get('account_name', 'N/A')
    site_table.rows[1].cells[0].text = 'Address:'
    site_table.rows[1].cells[1].text = site_info.get('address', 'N/A')
    
    contact = site_info.get('primary_contact', {})
    site_table.rows[2].cells[0].text = 'Contact Name:'
    site_table.rows[2].cells[1].text = contact.get('name', 'N/A')
    site_table.rows[3].cells[0].text = 'Contact Email:'
    site_table.rows[3].cells[1].text = contact.get('email', 'N/A')
    site_table.rows[4].cells[0].text = 'Contact Phone:'
    site_table.rows[4].cells[1].text = contact.get('phone', 'N/A')
    
    doc.add_paragraph()  # Spacer
    
    # Current State Assessment
    current_state = sow.get('current_state_assessment', {})
    doc.add_heading(current_state.get('title', 'Current State Assessment'), 1)
    
    for finding in current_state.get('findings', []):
        doc.add_heading(finding.get('category', ''), 2)
        p = doc.add_paragraph()
        p.add_run(finding.get('item', '')).bold = True
        if finding.get('details'):
            p.add_run(f"\n{finding.get('details')}")
    
    # Scope of Work
    scope = sow.get('scope_of_work', {})
    doc.add_heading(scope.get('title', 'Scope of Work'), 1)
    doc.add_paragraph(scope.get('description', ''))
    
    doc.add_heading('Deliverables:', 2)
    for deliverable in scope.get('deliverables', []):
        doc.add_paragraph(deliverable, style='List Bullet')
    
    doc.add_heading('Tasks:', 2)
    for task in scope.get('tasks', []):
        p = doc.add_paragraph(style='List Bullet')
        p.add_run(f"{task.get('task')} ").bold = True
        p.add_run(f"(Qty: {task.get('quantity')}): {task.get('description')}")
    
    # Materials and Equipment
    materials = sow.get('materials_and_equipment', {})
    doc.add_heading(materials.get('title', 'Materials and Equipment'), 1)
    
    doc.add_heading('Materials Required:', 2)
    mat_list = materials.get('materials', [])
    if mat_list:
        mat_table = doc.add_table(rows=len(mat_list)+1, cols=2)
        mat_table.style = 'Light Grid Accent 1'
        mat_table.rows[0].cells[0].text = 'Item'
        mat_table.rows[0].cells[1].text = 'Quantity'
        for i, mat in enumerate(mat_list, 1):
            mat_table.rows[i].cells[0].text = mat.get('item', '')
            mat_table.rows[i].cells[1].text = str(mat.get('quantity', 'TBD'))
    
    doc.add_heading('Tools Required:', 2)
    for tool in materials.get('tools', []):
        doc.add_paragraph(tool, style='List Bullet')
    
    # Timeline
    timeline = sow.get('timeline_estimate', {})
    doc.add_heading(timeline.get('title', 'Timeline Estimate'), 1)
    p = doc.add_paragraph()
    p.add_run('Estimated Hours: ').bold = True
    p.add_run(f"{timeline.get('estimated_hours', 0)}\n")
    p.add_run('Estimated Days: ').bold = True
    p.add_run(f"{timeline.get('estimated_days', 0)}\n")
    p.add_run('Technicians Required: ').bold = True
    p.add_run(f"{timeline.get('technicians_required', 1)}")
    
    # Cost Estimate
    cost = sow.get('cost_estimate', {})
    doc.add_heading(cost.get('title', 'Cost Estimate'), 1)
    
    labor = cost.get('labor', {})
    materials_cost = cost.get('materials', {})
    
    cost_table = doc.add_table(rows=4, cols=3)
    cost_table.style = 'Light Grid Accent 1'
    cost_table.rows[0].cells[0].text = 'Description'
    cost_table.rows[0].cells[1].text = 'Details'
    cost_table.rows[0].cells[2].text = 'Amount'
    
    cost_table.rows[1].cells[0].text = 'Labor'
    cost_table.rows[1].cells[1].text = f"{labor.get('hours', 0)} hrs @ ${labor.get('rate', 0):.2f}/hr"
    cost_table.rows[1].cells[2].text = f"${labor.get('subtotal', 0):.2f}"
    
    cost_table.rows[2].cells[0].text = 'Materials'
    cost_table.rows[2].cells[1].text = 'Estimated materials cost'
    cost_table.rows[2].cells[2].text = f"${materials_cost.get('estimated_cost', 0):.2f}"
    
    cost_table.rows[3].cells[1].text = 'TOTAL ESTIMATE'
    cost_table.rows[3].cells[2].text = f"${cost.get('total', 0):.2f}"
    
    doc.add_paragraph(cost.get('notes', ''), style='Intense Quote')
    
    # Page Break
    doc.add_page_break()
    
    # Terms and Conditions
    terms = sow.get('terms_and_conditions', {})
    doc.add_heading(terms.get('title', 'Terms and Conditions'), 1)
    
    for section in terms.get('sections', []):
        doc.add_heading(section.get('heading', ''), 2)
        doc.add_paragraph(section.get('content', ''))
    
    # Footer note
    doc.add_paragraph()
    doc.add_paragraph('This document is a preliminary statement of work based on site survey findings. Final scope and costs will be confirmed upon project commencement.', style='Intense Quote')
    
    # Save to buffer
    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer
