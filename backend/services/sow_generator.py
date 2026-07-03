"""
Statement of Work (SOW) Generation Service
Generates professional SOW documents from survey data in multiple formats.
"""
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import json
import logging

logger = logging.getLogger(__name__)


def generate_sow_from_survey(survey_data: Dict[str, Any], job: Dict[str, Any], account: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate a structured SOW from survey form data.
    Returns a dictionary that can be serialized to JSON, PDF, DOCX, or Markdown.
    """
    
    sow = {
        "metadata": {
            "document_type": "Statement of Work",
            "version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "job_id": job.get("job_id"),
            "account_id": account.get("account_id"),
            "survey_date": job.get("scheduled_date") or datetime.now(timezone.utc).date().isoformat(),
        },
        "executive_summary": _generate_executive_summary(survey_data, job, account),
        "site_information": _generate_site_information(survey_data, account),
        "current_state_assessment": _generate_current_state(survey_data, job),
        "scope_of_work": _generate_scope_of_work(survey_data, job),
        "materials_and_equipment": _generate_materials_list(survey_data, job),
        "timeline_estimate": _generate_timeline(survey_data, job),
        "cost_estimate": _generate_cost_estimate(survey_data, job),
        "terms_and_conditions": _generate_terms(),
    }
    
    return sow


def _generate_executive_summary(survey_data: Dict, job: Dict, account: Dict) -> Dict[str, Any]:
    """Generate executive summary section."""
    job_type_name = job.get("job_type_name", "Field Services")
    account_name = account.get("name", "Customer")
    
    summary_text = f"""This Statement of Work outlines the proposed {job_type_name} engagement for {account_name}. 
This document is based on a comprehensive site survey conducted on {job.get('scheduled_date', 'TBD')}.

The survey identified the current state of infrastructure, assessed requirements, and determined 
the scope of work necessary to successfully complete this project."""
    
    return {
        "title": "Executive Summary",
        "content": summary_text,
        "job_type": job.get("job_type_name"),
        "account_name": account_name,
        "survey_date": job.get("scheduled_date"),
    }


def _generate_site_information(survey_data: Dict, account: Dict) -> Dict[str, Any]:
    """Extract site information from survey."""
    site_info = {
        "title": "Site Information",
        "account_name": account.get("name"),
        "address": account.get("address", "Address not provided"),
        "primary_contact": {
            "name": survey_data.get("point_of_contact_name", "TBD"),
            "email": survey_data.get("point_of_contact_email", "TBD"),
            "phone": survey_data.get("point_of_contact_phone", "TBD"),
        },
    }
    
    # Add site profile data if available
    site_profile = account.get("site_profile", {})
    if site_profile:
        site_info["site_profile"] = {
            "mdf_location": site_profile.get("mdf_location"),
            "mpoe_location": site_profile.get("mpoe_location"),
            "isp_details": site_profile.get("isp_details"),
            "network_equipment": site_profile.get("network_equipment", []),
        }
    
    return site_info


def _generate_current_state(survey_data: Dict, job: Dict) -> Dict[str, Any]:
    """Generate current state assessment from survey responses."""
    job_type = job.get("job_type", "general")
    
    current_state = {
        "title": "Current State Assessment",
        "job_type": job_type,
        "findings": [],
    }
    
    # Extract findings based on job type
    if "cable_install" in job_type.lower():
        current_state["findings"].extend(_extract_cable_findings(survey_data))
    elif "fiber" in job_type.lower():
        current_state["findings"].extend(_extract_fiber_findings(survey_data))
    elif "device" in job_type.lower():
        current_state["findings"].extend(_extract_device_findings(survey_data))
    elif "rack" in job_type.lower():
        current_state["findings"].extend(_extract_rack_findings(survey_data))
    else:
        current_state["findings"].extend(_extract_general_findings(survey_data))
    
    return current_state


def _extract_cable_findings(survey_data: Dict) -> list:
    """Extract cable install survey findings."""
    findings = []
    
    if survey_data.get("existing_cabling"):
        cable_types = survey_data.get("existing_cabling_types", [])
        findings.append({
            "category": "Existing Infrastructure",
            "item": f"Existing cabling present: {', '.join(cable_types) if cable_types else 'Yes'}",
            "details": f"Cable quantity: {survey_data.get('cable_quantity', 'Not specified')}"
        })
    
    if survey_data.get("ceiling_type"):
        findings.append({
            "category": "Site Conditions",
            "item": f"Ceiling type: {survey_data.get('ceiling_type')}",
            "details": f"Height: {survey_data.get('ceiling_height', 'Not specified')}"
        })
    
    if survey_data.get("mdf_location"):
        findings.append({
            "category": "Network Infrastructure",
            "item": f"MDF Location: {survey_data.get('mdf_location')}",
            "details": f"Patch panel present: {survey_data.get('patch_panel', 'Unknown')}"
        })
    
    return findings


def _extract_fiber_findings(survey_data: Dict) -> list:
    """Extract fiber survey findings."""
    findings = []
    
    if survey_data.get("demarc_location"):
        findings.append({
            "category": "Fiber Demarc",
            "item": f"Demarc location: {survey_data.get('demarc_location')}",
            "details": f"Connector type: {survey_data.get('demarc_connector', 'TBD')}"
        })
    
    if survey_data.get("fiber_strand_count"):
        findings.append({
            "category": "Fiber Infrastructure",
            "item": f"Total fiber strands: {survey_data.get('fiber_strand_count')}",
            "details": f"Available strands: {survey_data.get('available_strands', 'TBD')}, Type: {survey_data.get('fiber_type', 'TBD')}"
        })
    
    return findings


def _extract_device_findings(survey_data: Dict) -> list:
    """Extract device install survey findings."""
    findings = []
    
    if survey_data.get("device_types"):
        findings.append({
            "category": "Devices to Install",
            "item": survey_data.get("device_types"),
            "details": f"Install location: {survey_data.get('install_location', 'TBD')}"
        })
    
    if survey_data.get("device_mount"):
        findings.append({
            "category": "Mounting",
            "item": f"Mount type: {survey_data.get('device_mount')}",
            "details": f"Ladder/Lift required: {survey_data.get('ladder_or_lift', 'TBD')}"
        })
    
    return findings


def _extract_rack_findings(survey_data: Dict) -> list:
    """Extract rack cleanup survey findings."""
    findings = []
    
    if survey_data.get("rack_location"):
        findings.append({
            "category": "Rack Information",
            "item": f"Rack location: {survey_data.get('rack_location')}",
            "details": f"Make/Model: {survey_data.get('rack_make_model', 'TBD')}"
        })
    
    if survey_data.get("cabling_state"):
        findings.append({
            "category": "Current State",
            "item": f"Cabling condition: {survey_data.get('cabling_state')}",
            "details": f"Available rack U's: {survey_data.get('rack_us', 'TBD')}"
        })
    
    return findings


def _extract_general_findings(survey_data: Dict) -> list:
    """Extract general survey findings."""
    findings = []
    
    if survey_data.get("general_observations"):
        findings.append({
            "category": "General Observations",
            "item": survey_data.get("general_observations"),
            "details": ""
        })
    
    if survey_data.get("isp_carriers"):
        findings.append({
            "category": "Internet Service",
            "item": f"ISP: {survey_data.get('isp_carriers')}",
            "details": ""
        })
    
    return findings


def _generate_scope_of_work(survey_data: Dict, job: Dict) -> Dict[str, Any]:
    """Generate scope of work section."""
    scope = {
        "title": "Scope of Work",
        "description": survey_data.get("scope_of_work_description", ""),
        "deliverables": [],
        "tasks": [],
    }
    
    # Extract deliverables from survey
    if survey_data.get("deliverables"):
        if isinstance(survey_data["deliverables"], list):
            scope["deliverables"] = survey_data["deliverables"]
        else:
            scope["deliverables"] = [survey_data["deliverables"]]
    
    # Add specific tasks based on survey responses
    if survey_data.get("cable_drops"):
        scope["tasks"].append({
            "task": "Cable Installation",
            "quantity": survey_data.get("cable_drops"),
            "description": f"Install {survey_data.get('cable_drops')} cable drops at specified locations"
        })
    
    if survey_data.get("devices_to_install"):
        scope["tasks"].append({
            "task": "Device Installation",
            "quantity": survey_data.get("devices_to_install"),
            "description": survey_data.get("device_description", "Install and configure devices")
        })
    
    return scope


def _generate_materials_list(survey_data: Dict, job: Dict) -> Dict[str, Any]:
    """Generate materials and equipment list."""
    materials = {
        "title": "Materials and Equipment Required",
        "materials": [],
        "equipment": [],
        "tools": [],
    }
    
    # Extract materials from survey
    if survey_data.get("materials_needed"):
        materials_list = survey_data.get("materials_needed")
        if isinstance(materials_list, str):
            materials["materials"] = [{"item": m.strip(), "quantity": "TBD"} for m in materials_list.split(",")]
        elif isinstance(materials_list, list):
            materials["materials"] = [{"item": m, "quantity": "TBD"} for m in materials_list]
    
    # Extract tools from survey
    if survey_data.get("tools_required"):
        tools_list = survey_data.get("tools_required")
        if isinstance(tools_list, str):
            materials["tools"] = [t.strip() for t in tools_list.split(",")]
        elif isinstance(tools_list, list):
            materials["tools"] = tools_list
    
    # Cable types needed
    if survey_data.get("cable_types_needed"):
        cable_types = survey_data.get("cable_types_needed", [])
        for cable_type in cable_types:
            materials["materials"].append({
                "item": f"{cable_type} Cable",
                "quantity": survey_data.get(f"{cable_type.lower()}_cable_length", "TBD")
            })
    
    return materials


def _generate_timeline(survey_data: Dict, job: Dict) -> Dict[str, Any]:
    """Generate project timeline estimate."""
    timeline = {
        "title": "Estimated Timeline",
        "estimated_hours": survey_data.get("hour_estimate") or job.get("estimated_hours", 0),
        "estimated_days": 0,
        "technicians_required": survey_data.get("technicians_required", 1),
        "phases": [],
    }
    
    # Calculate days from hours
    hours = float(timeline["estimated_hours"])
    techs = int(timeline["technicians_required"])
    if hours > 0 and techs > 0:
        hours_per_day = 8
        timeline["estimated_days"] = round((hours / techs) / hours_per_day, 1)
    
    # Add phases if applicable
    if survey_data.get("multi_phase"):
        timeline["phases"] = survey_data.get("phases", [])
    
    return timeline


def _generate_cost_estimate(survey_data: Dict, job: Dict) -> Dict[str, Any]:
    """Generate cost estimate section."""
    cost_estimate = {
        "title": "Cost Estimate",
        "labor": {
            "hours": survey_data.get("hour_estimate") or job.get("estimated_hours", 0),
            "rate": job.get("hourly_rate", 0),
            "subtotal": 0,
        },
        "materials": {
            "estimated_cost": survey_data.get("materials_cost", 0) or job.get("material_cost", 0),
        },
        "total": 0,
        "notes": "This is a preliminary estimate based on site survey findings. Final costs may vary based on actual conditions and scope changes.",
    }
    
    # Calculate totals
    labor_hours = float(cost_estimate["labor"]["hours"])
    labor_rate = float(cost_estimate["labor"]["rate"])
    cost_estimate["labor"]["subtotal"] = labor_hours * labor_rate
    
    materials_cost = float(cost_estimate["materials"]["estimated_cost"])
    cost_estimate["total"] = cost_estimate["labor"]["subtotal"] + materials_cost
    
    return cost_estimate


def _generate_terms() -> Dict[str, Any]:
    """Generate standard terms and conditions."""
    return {
        "title": "Terms and Conditions",
        "sections": [
            {
                "heading": "Scope Changes",
                "content": "Any changes to the scope of work outlined in this SOW must be agreed upon in writing by both parties and may result in additional costs and timeline adjustments."
            },
            {
                "heading": "Site Access",
                "content": "Client agrees to provide full site access during normal business hours or as mutually agreed upon. Any delays due to lack of access may result in timeline extensions."
            },
            {
                "heading": "Payment Terms",
                "content": "Payment terms will be established per the contract agreement. Milestone-based billing may apply for larger projects."
            },
            {
                "heading": "Warranty",
                "content": "All work is guaranteed for 90 days from project completion. Materials and equipment warranties are as provided by manufacturers."
            },
            {
                "heading": "Safety and Compliance",
                "content": "All work will be performed in accordance with local codes, safety regulations, and industry best practices."
            },
        ]
    }


def sow_to_markdown(sow: Dict[str, Any]) -> str:
    """Convert SOW to Markdown format."""
    md = []
    
    # Title
    md.append("# STATEMENT OF WORK\n")
    md.append(f"**Generated:** {sow['metadata']['generated_at']}\n")
    md.append(f"**Version:** {sow['metadata']['version']}\n")
    md.append("---\n")
    
    # Executive Summary
    exec_summary = sow.get("executive_summary", {})
    md.append(f"## {exec_summary.get('title', 'Executive Summary')}\n")
    md.append(f"{exec_summary.get('content', '')}\n\n")
    
    # Site Information
    site_info = sow.get("site_information", {})
    md.append(f"## {site_info.get('title', 'Site Information')}\n")
    md.append(f"**Account:** {site_info.get('account_name')}\n")
    md.append(f"**Address:** {site_info.get('address')}\n\n")
    md.append("**Primary Contact:**\n")
    contact = site_info.get("primary_contact", {})
    md.append(f"- Name: {contact.get('name')}\n")
    md.append(f"- Email: {contact.get('email')}\n")
    md.append(f"- Phone: {contact.get('phone')}\n\n")
    
    # Current State Assessment
    current_state = sow.get("current_state_assessment", {})
    md.append(f"## {current_state.get('title', 'Current State Assessment')}\n")
    for finding in current_state.get("findings", []):
        md.append(f"### {finding.get('category')}\n")
        md.append(f"- **{finding.get('item')}**\n")
        if finding.get('details'):
            md.append(f"  - {finding.get('details')}\n")
        md.append("\n")
    
    # Scope of Work
    scope = sow.get("scope_of_work", {})
    md.append(f"## {scope.get('title', 'Scope of Work')}\n")
    md.append(f"{scope.get('description', '')}\n\n")
    md.append("### Deliverables\n")
    for deliverable in scope.get("deliverables", []):
        md.append(f"- {deliverable}\n")
    md.append("\n")
    md.append("### Tasks\n")
    for task in scope.get("tasks", []):
        md.append(f"- **{task.get('task')}** (Qty: {task.get('quantity')}): {task.get('description')}\n")
    md.append("\n")
    
    # Materials
    materials = sow.get("materials_and_equipment", {})
    md.append(f"## {materials.get('title', 'Materials and Equipment')}\n")
    md.append("### Materials\n")
    for mat in materials.get("materials", []):
        md.append(f"- {mat.get('item')} (Qty: {mat.get('quantity', 'TBD')})\n")
    md.append("\n### Tools\n")
    for tool in materials.get("tools", []):
        md.append(f"- {tool}\n")
    md.append("\n")
    
    # Timeline
    timeline = sow.get("timeline_estimate", {})
    md.append(f"## {timeline.get('title', 'Timeline Estimate')}\n")
    md.append(f"- **Estimated Hours:** {timeline.get('estimated_hours')}\n")
    md.append(f"- **Estimated Days:** {timeline.get('estimated_days')}\n")
    md.append(f"- **Technicians Required:** {timeline.get('technicians_required')}\n\n")
    
    # Cost Estimate
    cost = sow.get("cost_estimate", {})
    md.append(f"## {cost.get('title', 'Cost Estimate')}\n")
    labor = cost.get("labor", {})
    md.append("### Labor\n")
    md.append(f"- Hours: {labor.get('hours')}\n")
    md.append(f"- Rate: ${labor.get('rate'):.2f}/hr\n")
    md.append(f"- Subtotal: ${labor.get('subtotal', 0):.2f}\n\n")
    md.append("### Materials\n")
    materials_cost = cost.get("materials", {})
    md.append(f"- Estimated Cost: ${materials_cost.get('estimated_cost', 0):.2f}\n\n")
    md.append(f"### Total Estimated Cost: ${cost.get('total', 0):.2f}\n\n")
    md.append(f"*{cost.get('notes', '')}*\n\n")
    
    # Terms
    terms = sow.get("terms_and_conditions", {})
    md.append(f"## {terms.get('title', 'Terms and Conditions')}\n")
    for section in terms.get("sections", []):
        md.append(f"### {section.get('heading')}\n")
        md.append(f"{section.get('content')}\n\n")
    
    md.append("---\n")
    md.append("*This document is a preliminary statement of work based on site survey findings. Final scope and costs will be confirmed upon project commencement.*\n")
    
    return "".join(md)


def sow_to_json(sow: Dict[str, Any]) -> str:
    """Convert SOW to JSON format (AI-ingestible)."""
    return json.dumps(sow, indent=2, default=str)
