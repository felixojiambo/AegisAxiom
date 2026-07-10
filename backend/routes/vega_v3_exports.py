"""Vega v3 export endpoints — PDF + Google Doc + Markdown for the new artifact shape.

The v3 pipeline persists each analysis as a single ``ai_chat_artifacts`` doc
with ``artifact_type == "discovery_analysis_v3"``. Every design doc and EADD
is embedded inline (Samson's self-contained-artifact requirement), so these
endpoints read a single doc and render directly — no fetch-by-id round trips.

Three export targets:

    target=analysis        — the whole discovery report
    target=design_doc      — one workflow's AI Design Document (entity_id=workflow_id)
    target=eadd            — one app's Emergent App Design Document (entity_id=app_id)

Three formats:

    format=markdown        — paste-ready markdown (default for GET endpoints)
    format=pdf             — reportlab-rendered PDF (falls back to markdown if reportlab missing)
    POST /export/google-doc — Google Doc, same auth + Drive folder flow as v2
"""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from auth_helper import get_current_user, has_perm
from database import get_db
from services.vega_events import emit_vega_event

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Artifact lookup + IDOR guard
# ---------------------------------------------------------------------------


def _derive_shared_infrastructure(payload: dict) -> list[dict]:
    """Derive cross-app shared infrastructure from per-app integrations.

    Dev requirement: surface every system that appears in 2+ apps so the
    build team can spot QuickBooks-in-3-apps style sharing without scanning
    every app section. Falls back to ``payload.shared_infrastructure`` when
    Stage 4 has populated it explicitly.
    """
    explicit = payload.get("shared_infrastructure") or []
    if explicit:
        return explicit
    apps = payload.get("apps") or []
    counts: dict[str, list[str]] = {}
    notes: dict[str, str] = {}
    for app in apps:
        app_name = app.get("app_name") or app.get("app_id") or "?"
        seen = set()
        for integ in app.get("integrations") or []:
            sys_name = (integ.get("system_name") or "").strip()
            if not sys_name or sys_name.lower() in seen:
                continue
            seen.add(sys_name.lower())
            counts.setdefault(sys_name, []).append(app_name)
            if integ.get("notes") and sys_name not in notes:
                notes[sys_name] = integ["notes"]
    return [
        {
            "system_name": sys_name,
            "used_by_apps": names,
            "notes": notes.get(sys_name, ""),
        }
        for sys_name, names in sorted(counts.items())
        if len(names) >= 2
    ]


async def _load_v3_artifact(db, artifact_id: str, user_id: str) -> dict:
    art = await db.ai_chat_artifacts.find_one({"artifact_id": artifact_id}, {"_id": 0})
    if not art:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if art.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="You do not own this artifact")
    if art.get("artifact_type") != "discovery_analysis_v3":
        raise HTTPException(
            status_code=400,
            detail=f"Artifact is not a v3 discovery analysis (got {art.get('artifact_type')!r})",
        )
    return art


# ---------------------------------------------------------------------------
# Segment builder — returns list[(text, level)] where level=0 is body, 1-3 are headings.
# ---------------------------------------------------------------------------


def _segments_for_analysis(payload: dict) -> list[tuple[str, int]]:
    seg: list[tuple[str, int]] = []
    add = lambda t="", lv=0: seg.append((t, lv))
    bullet = lambda s, indent=1: add(("  " * indent) + "• " + s) if s else None

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    client = payload.get("client_company") or "Unknown"
    add(f"Vega Discovery Report — {client}", 1)
    add(f"Generated: {today}")
    add()

    flags = payload.get("session_flags") or {}
    add("Session Flags", 2)
    add(f"Technical contact: {flags.get('tech_contact') or flags.get('tech_contact_flag') or '—'}")
    add(f"Engineering capacity: {flags.get('engineering_capacity') if flags.get('engineering_capacity') is not None else flags.get('engineering_capacity_flag') or '—'}")
    add(f"IT/infosec gate: {flags.get('it_infosec_gate') if flags.get('it_infosec_gate') is not None else flags.get('it_infosec_gate_flag') or '—'}")
    add()

    # Shared Infrastructure — surface up top so the build team sees every
    # system that touches 2+ apps before they read individual app sections.
    shared = _derive_shared_infrastructure(payload)
    app_id_to_name = {a.get("app_id"): a.get("app_name") or a.get("app_id")
                      for a in payload.get("apps") or []}
    if shared:
        add("Shared Infrastructure (cross-app)", 2)
        add(
            "Systems referenced by more than one app in this engagement. Treat as "
            "shared services for sizing and integration sequencing — coordinate "
            "auth, rate limits, and provisioning once instead of per-app."
        )
        for row in shared:
            users = row.get("used_by_apps") or row.get("used_by_app_ids") or []
            if isinstance(users, list):
                users_str = ", ".join(app_id_to_name.get(u, u) for u in users)
            else:
                users_str = str(users)
            bullet(f"{row.get('system_name', '')} — used by: {users_str}")
            for label, key in (
                ("Read / write", "read_write"),
                ("Auth", "auth_method"),
                ("Access", "access_status"),
            ):
                v = row.get(key)
                if v and v != "unknown":
                    add(f"  {label}: {v}")
            if row.get("notes"):
                add(f"  Notes: {row['notes']}")
        add()

    workflows = payload.get("workflows") or []
    if workflows:
        add("Workflows", 2)
        for wf in workflows:
            add(wf.get("workflow_name", "Unknown workflow"), 3)
            if wf.get("partial_extraction_failed"):
                add(f"[Partial extraction: {wf.get('partial_extraction_reason') or 'detail call did not complete'}]")
            src = wf.get("source") or {}
            if src.get("quote"):
                add(f'"{src["quote"]}"')
            mmi = wf.get("mmi_hypothesis") or {}
            if mmi:
                add(
                    f"MMI: TSS={(mmi.get('tss') or {}).get('value', '?')} "
                    f"IE={(mmi.get('ie') or {}).get('value', '?')} "
                    f"Time={(mmi.get('time') or {}).get('value', '?')} "
                    f"Cost={(mmi.get('cost') or {}).get('value', '?')} "
                    f"Quality={(mmi.get('quality') or {}).get('value', '?')} "
                    f"Satisfaction={(mmi.get('satisfaction') or {}).get('value', '?')} "
                    f"| Score={mmi.get('score') if mmi.get('score') is not None else 'pending'}"
                )
                add("MMI Reasoning:")
                for dim_key in ("tss", "ie", "time", "cost", "quality", "satisfaction"):
                    dim = mmi.get(dim_key) or {}
                    reasoning = dim.get("reasoning")
                    if reasoning:
                        conf = dim.get("confidence")
                        prefix = f"{dim_key.upper()}"
                        if conf:
                            prefix += f" ({conf})"
                        bullet(f"{prefix}: {reasoning}")
                if mmi.get("needs_client_input"):
                    add("Needs client input:")
                    for q in mmi["needs_client_input"]:
                        bullet(q)
            ac = wf.get("acceptance_criteria") or []
            if ac:
                add("Acceptance Criteria:")
                for c in ac:
                    flow = c.get("flow_name") or ""
                    crit = c.get("success_when") or "(success criterion not captured)"
                    bullet(f"{flow}: {crit}" if flow else crit)
            steps = wf.get("process_steps") or []
            if steps:
                add("Process Steps:")
                for s in steps:
                    name = s.get("name") or s.get("id") or ""
                    desc = s.get("description") or ""
                    bullet(f"{name}{(' — ' + desc) if desc else ''}")
            decisions = wf.get("decision_logic") or []
            if decisions:
                add("Decision Logic:")
                for d in decisions:
                    name = d.get("name") or d.get("id", "")
                    desc = d.get("description") or ""
                    head = f"{name}{(' — ' + desc) if desc else ''}"
                    bullet(head)
                    for b in (d.get("branches") or []):
                        cond = b.get("condition") or ""
                        leads = b.get("leads_to_step_id") or ""
                        bullet(f"{cond}{(' → ' + leads) if leads else ''}", indent=2)
            add()

    apps = payload.get("apps") or []
    if apps:
        add("Apps", 2)
        for app in apps:
            add(app.get("app_name", "Unknown app"), 3)
            if app.get("complexity"):
                add(f"Complexity: {app['complexity']}")
            personas = app.get("personas") or []
            if personas:
                add("Personas:")
                for p in personas:
                    role = p.get("role") or "unknown"
                    bits = []
                    if p.get("count") is not None:
                        bits.append(f"count={p['count']}")
                    if p.get("device"):
                        bits.append(f"device={p['device']}")
                    if p.get("technical_literacy"):
                        bits.append(f"tech={p['technical_literacy']}")
                    suffix = (" — " + ", ".join(bits)) if bits else ""
                    bullet(f"{role}{suffix}")
            integrations = app.get("integrations") or []
            if integrations:
                add("Integrations:")
                for it in integrations:
                    bucket = it.get("bucket")
                    bucket_label = (
                        f"bucket {bucket}" if bucket is not None else "bucket ?"
                    )
                    notes = it.get("notes") or ""
                    bullet(f"{it.get('system_name', '')} — {bucket_label}{(' — ' + notes) if notes else ''}")
            scale = app.get("scale_profile") or {}
            scale_bits = [
                ("Volume", scale.get("transaction_volume")),
                ("Retention", scale.get("retention_window")),
                ("Growth", scale.get("growth_trajectory")),
                ("Pain", scale.get("speed_or_storage_pain")),
                ("Inference", scale.get("inference")),
            ]
            scale_bits = [(label, val) for label, val in scale_bits if val]
            if scale_bits:
                add("Scale:")
                for label, val in scale_bits:
                    bullet(f"{label}: {val}")
            nfrs = app.get("nfrs") or {}
            nfr_bits = [
                ("Devices", nfrs.get("device_support")),
                ("Browsers", nfrs.get("browser_support")),
                ("Data freshness", nfrs.get("data_freshness")),
                ("RBAC", nfrs.get("rbac")),
                ("SLA", nfrs.get("sla")),
                ("Compliance", nfrs.get("compliance")),
                ("Multi-region", nfrs.get("multi_region")),
            ]
            nfr_bits = [(label, val) for label, val in nfr_bits if val]
            if nfr_bits:
                add("NFRs:")
                for label, val in nfr_bits:
                    bullet(f"{label}: {val}")
            add()

    open_items = payload.get("open_items") or []
    if open_items:
        add("Open Items", 2)
        for oi in open_items:
            add(f"[{oi.get('flag', '')}] {oi.get('label', '')} ({oi.get('location', '')})")
            if oi.get("detail"):
                bullet(oi["detail"])
        add()

    return seg


def _segments_for_design_doc(payload: dict, workflow_id: str) -> list[tuple[str, int]]:
    workflows = payload.get("workflows") or []
    wf = next((w for w in workflows if w.get("workflow_id") == workflow_id), None)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found in artifact")
    doc = wf.get("design_doc")
    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"Design document not generated for workflow {workflow_id} "
            f"(status={wf.get('design_doc_status')})",
        )

    seg: list[tuple[str, int]] = []
    add = lambda t="", lv=0: seg.append((t, lv))
    bullet = lambda s, indent=1: add(("  " * indent) + "• " + s) if s else None

    wf_name = wf.get("workflow_name", "Unknown workflow")
    client = payload.get("client_company") or "Unknown client"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    add(f"AI Design Document — {wf_name}", 1)
    add(f"Client: {client}")
    add(f"Generated: {today}")
    add()

    if doc.get("executive_summary"):
        add("Executive Summary", 2)
        add(doc["executive_summary"])
        add()

    cs = doc.get("current_state") or {}
    if cs:
        add("Current State", 2)
        if cs.get("process_description"):
            add(cs["process_description"])
        if cs.get("manual_effort"):
            add(f"Manual effort: {cs['manual_effort']}")
        if cs.get("error_rate_notes"):
            add(f"Error rate / quality notes: {cs['error_rate_notes']}")
        if cs.get("pain_points"):
            add("Pain points:")
            for p in cs["pain_points"]:
                bullet(p)
        if cs.get("systems_involved"):
            add(f"Systems involved: {', '.join(cs['systems_involved'])}")
        add()

    ps = doc.get("proposed_ai_solution") or {}
    if ps:
        add("Proposed AI Solution", 2)
        if ps.get("solution_description"):
            add(ps["solution_description"])
        if ps.get("ai_role"):
            add(f"AI role: {ps['ai_role']}")
        if ps.get("human_role"):
            add(f"Human role: {ps['human_role']}")
        if ps.get("automation_percentage"):
            add(f"Estimated automation: {ps['automation_percentage']}")
        if ps.get("key_capabilities"):
            add("Key capabilities:")
            for c in ps["key_capabilities"]:
                bullet(c)
        add()

    if doc.get("bpmn_process_narrative"):
        add("Process Narrative", 2)
        add(doc["bpmn_process_narrative"])
        add()

    trig = doc.get("trigger_specification") or {}
    if trig:
        add("Trigger", 2)
        if trig.get("type"):
            add(f"Type: {trig['type']}")
        if trig.get("frequency"):
            add(f"Frequency: {trig['frequency']}")
        if trig.get("data_source"):
            add(f"Source: {trig['data_source']}")
        add()

    df = doc.get("data_flow") or {}
    if df:
        add("Data Flow", 2)
        if df.get("inputs"):
            add("Inputs:")
            for inp in df["inputs"]:
                if isinstance(inp, dict):
                    bullet(f"{inp.get('name', '')} ← {inp.get('source', '')}"
                           + (f" ({inp['format']})" if inp.get('format') else ""))
                else:
                    bullet(str(inp))
        if df.get("processing"):
            add(f"Processing: {df['processing']}")
        if df.get("outputs"):
            add("Outputs:")
            for out in df["outputs"]:
                if isinstance(out, dict):
                    bullet(f"{out.get('name', '')} → {out.get('destination', '')}"
                           + (f" ({out['format']})" if out.get('format') else ""))
                else:
                    bullet(str(out))
        if df.get("data_sensitivity"):
            add(f"Data sensitivity: {df['data_sensitivity']}")
        add()

    irs = doc.get("integration_requirements") or []
    if irs:
        add("Integration Requirements", 2)
        for ir in irs:
            line = f"{ir.get('system_name', '')} — {ir.get('integration_type', '')} — {ir.get('data_exchanged', '')}"
            if ir.get("complexity"):
                line += f" (complexity: {ir['complexity']})"
            bullet(line)
            if ir.get("notes"):
                add(f"  Notes: {ir['notes']}")
        add()

    if doc.get("cross_solution_handoffs"):
        add("Cross-Solution Handoffs", 2)
        handoffs = doc["cross_solution_handoffs"]
        if isinstance(handoffs, list):
            for h in handoffs:
                if isinstance(h, dict):
                    bullet(f"{h.get('target_name', '')}: {h.get('description', '')}")
                else:
                    bullet(str(h))
        else:
            add(str(handoffs))
        add()

    htps = doc.get("human_touchpoints") or []
    if htps:
        add("Human Touchpoints", 2)
        for h in htps:
            if isinstance(h, dict):
                line = h.get("touchpoint", "")
                if h.get("frequency"):
                    line += f" ({h['frequency']})"
                bullet(line)
                if h.get("reason"):
                    add(f"  Reason: {h['reason']}")
            else:
                bullet(str(h))
        add()

    mj = doc.get("mmi_justification") or {}
    if mj:
        add("MMI Justification", 2)
        if mj.get("overall_score") is not None:
            add(f"Overall score: {mj['overall_score']}")
        for dim in ("tss", "ie", "time", "cost", "quality", "satisfaction"):
            v = mj.get(f"{dim}_rationale")
            if v:
                bullet(f"{dim.upper()}: {v}")
        add()

    impl = doc.get("implementation_considerations") or {}
    if impl:
        add("Implementation Considerations", 2)
        if impl.get("estimated_complexity"):
            add(f"Estimated complexity: {impl['estimated_complexity']}")
        for label, key in (
            ("Risks", "risks"),
            ("Dependencies", "dependencies"),
            ("Assumptions", "assumptions"),
            ("Open questions", "open_questions"),
        ):
            items = impl.get(key) or []
            if items:
                add(f"{label}:")
                for it in items:
                    bullet(str(it))
        add()

    if doc.get("aegisaxiom_module_dependencies"):
        add("AEGISAXIOM Module Dependencies", 2)
        for m in doc["aegisaxiom_module_dependencies"]:
            bullet(str(m))
        add()

    return seg


def _segments_for_eadd(payload: dict, app_id: str) -> list[tuple[str, int]]:
    """Build the App Design Document for a dev team.

    Order: lead with what a developer needs to actually build the application
    (overview, personas, flows, data model, integrations, NFRs, scale,
    architecture notes, out-of-scope). The starter prompt comes LAST as an
    appendix for bootstrapping a POC — it is not the design itself.
    """
    apps = payload.get("apps") or []
    app = next((a for a in apps if a.get("app_id") == app_id), None)
    if not app:
        raise HTTPException(status_code=404, detail=f"App {app_id} not found in artifact")
    eadd = app.get("eadd_doc")
    if not eadd:
        raise HTTPException(
            status_code=404,
            detail=f"App design document not generated for app {app_id} "
            f"(status={app.get('design_doc_status')})",
        )

    seg: list[tuple[str, int]] = []
    add = lambda t="", lv=0: seg.append((t, lv))
    bullet = lambda s, indent=1: add(("  " * indent) + "• " + s) if s else None

    app_name = app.get("app_name", "Unknown app")
    client = payload.get("client_company") or "Unknown client"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    add(f"App Design Document — {app_name}", 1)
    add(f"Client: {client}")
    add(f"Generated: {today}")
    add()

    # 1. Overview ─────────────────────────────────────────────
    add("1. Overview", 2)
    if app.get("app_pitch_summary"):
        add(app["app_pitch_summary"])
    if app.get("primary_persona_hint"):
        add(f"Primary persona: {app['primary_persona_hint']}")
    if app.get("complexity"):
        # Auto-derive entity/integration counts so dev team can sanity-check sizing.
        entity_count = len((app.get("eadd_doc") or {}).get("data_model") or [])
        integ_count = len(app.get("integrations") or [])
        flow_count = len((app.get("eadd_doc") or {}).get("core_flows") or [])
        add(f"Complexity: {app['complexity']} · "
            f"{entity_count} entities · {integ_count} integrations · {flow_count} core flows")
    if app.get("linked_workflow_ids"):
        wf_by_id = {w.get("workflow_id"): w for w in payload.get("workflows") or []}
        names = [(wf_by_id.get(wid) or {}).get("workflow_name") or wid for wid in app["linked_workflow_ids"]]
        add(f"Linked AI workflows: {', '.join(names)}")
    add()

    # 1b. Source Provenance ───────────────────────────────────
    src = app.get("source") or {}
    extra_quotes = app.get("additional_source_quotes") or []
    linked_ids = app.get("linked_workflow_ids") or []
    direct_pain_refs = app.get("pain_point_refs") or []
    workflow_pain_points: list[str] = []
    wf_by_id = {w.get("workflow_id"): w for w in payload.get("workflows") or []}
    for wid in linked_ids:
        wf = wf_by_id.get(wid) or {}
        cs = (wf.get("design_doc") or {}).get("current_state") or {}
        for pp in cs.get("pain_points") or []:
            if pp and pp not in workflow_pain_points:
                workflow_pain_points.append(pp)
    if src or extra_quotes or direct_pain_refs or workflow_pain_points:
        add("1b. Source Provenance", 2)
        if src.get("transcript_filename"):
            ts = src.get("timestamp_seconds")
            ts_str = f" @ {int(ts)}s" if isinstance(ts, (int, float)) else ""
            add(f"Originating call: {src['transcript_filename']}{ts_str}")
        if src.get("speaker"):
            add(f"Speaker: {src['speaker']}")
        if src.get("quote"):
            add(f'"{src["quote"]}"')
        if direct_pain_refs:
            add("Pain points this app directly addresses (transcript quotes):")
            for pp in direct_pain_refs:
                bullet(pp)
        if extra_quotes:
            add("Additional quotes:")
            for q in extra_quotes[:5]:
                if isinstance(q, dict) and q.get("quote"):
                    bullet(f'"{q["quote"]}"' + (f" — {q.get('speaker')}" if q.get("speaker") else ""))
        if workflow_pain_points:
            add("Pain points from linked AI workflows:")
            for pp in workflow_pain_points:
                bullet(pp)
        add()

    # 1c. Shared Infrastructure used by THIS app ──────────────
    shared = _derive_shared_infrastructure(payload)
    if shared:
        my_shared = [
            row for row in shared
            if app_name in (row.get("used_by_apps") or row.get("used_by_app_ids") or [])
        ]
        if my_shared:
            add("1c. Shared Infrastructure (used by this app)", 2)
            add(
                "Systems below are also used by other apps in this engagement. "
                "Coordinate auth, rate limits, and provisioning across teams."
            )
            for row in my_shared:
                users = [u for u in (row.get("used_by_apps") or []) if u != app_name]
                bullet(f"{row.get('system_name', '')} — also used by: {', '.join(users)}")
            add()

    # 1d. Linked AI Workflows — embedded summaries ────────────
    if linked_ids:
        add("1d. Linked AI Workflow Definitions", 2)
        add(
            "Embedded summaries of the AI workflows this app supports. Build the app "
            "to consume their outputs and surface their decisions."
        )
        for wid in linked_ids:
            wf = wf_by_id.get(wid)
            if not wf:
                bullet(f"{wid} (workflow not found in artifact)")
                continue
            wf_name = wf.get("workflow_name") or wid
            add(wf_name, 3)
            wf_doc = wf.get("design_doc") or {}
            if wf_doc.get("executive_summary"):
                add(wf_doc["executive_summary"])
            ps = wf_doc.get("proposed_ai_solution") or {}
            if ps.get("solution_description"):
                add(f"What it does: {ps['solution_description']}")
            df = wf_doc.get("data_flow") or {}
            if df.get("outputs"):
                add("Outputs this app consumes:")
                for out in df["outputs"]:
                    if isinstance(out, dict):
                        bullet(f"{out.get('name', '')} → {out.get('destination', '')}")
            ac = wf.get("acceptance_criteria") or []
            if ac:
                add("Workflow acceptance criteria:")
                for c in ac:
                    flow = c.get("flow_name") or ""
                    crit = c.get("success_when") or ""
                    bullet(f"{flow}: {crit}" if flow else crit)
        add()

    # 2. Personas ─────────────────────────────────────────────
    personas = app.get("personas") or []
    if personas:
        add("2. Personas", 2)
        add(
            "Each persona carries user count, technical literacy, primary "
            "device, concurrent-usage pattern, and accessibility needs so the "
            "build team can size load, choose components, and meet WCAG "
            "obligations without re-running discovery."
        )
        for p in personas:
            if not isinstance(p, dict):
                continue
            role = p.get("role") or "(unnamed role)"
            add(role, 3)
            bullet(f"User count: {p.get('count') if p.get('count') is not None else '(NOT CAPTURED)'}")
            bullet(f"Technical literacy: {p.get('technical_literacy') or '(NOT CAPTURED)'}")
            bullet(f"Primary device: {p.get('device') or '(NOT CAPTURED)'}")
            bullet(f"Concurrent usage: {p.get('concurrent_usage') or '(NOT CAPTURED)'}")
            bullet(f"Accessibility needs: {p.get('accessibility_needs') or '(NOT CAPTURED)'}")
            if p.get("frequency_of_use"):
                bullet(f"Frequency: {p['frequency_of_use']}")
            if p.get("primary_actions"):
                actions = p["primary_actions"]
                if isinstance(actions, list):
                    actions = ", ".join(actions)
                bullet(f"Actions: {actions}")
        add()

    # 3. Core User Flows ──────────────────────────────────────
    if eadd.get("core_flows"):
        add("3. Core User Flows", 2)
        add(
            "Each flow MUST carry a 'success when' acceptance criterion so the "
            "build team can write tests against it without re-running discovery."
        )
        for i, flow in enumerate(eadd["core_flows"], 1):
            if isinstance(flow, dict):
                title = flow.get("name") or flow.get("title") or f"Flow {i}"
                add(f"{i}. {title}", 3)
                if flow.get("description"):
                    add(flow["description"])
                steps = flow.get("steps")
                if isinstance(steps, list):
                    for step in steps:
                        bullet(str(step))
                elif isinstance(steps, str):
                    add(steps)
                success = (
                    flow.get("success_when")
                    or flow.get("success_criteria")
                    or flow.get("acceptance_criteria")
                )
                if success:
                    add(f"Success when: {success}")
                else:
                    add("Success when: (NOT CAPTURED — re-run analysis to extract)")
            else:
                # Legacy string-only flows: no success criterion captured at all.
                add(f"{i}. {flow}", 3)
                add("Success when: (NOT CAPTURED — re-run analysis to extract)")
        add()

    # 4. Data Model ───────────────────────────────────────────
    if eadd.get("data_model"):
        add("4. Data Model", 2)
        dm = eadd["data_model"]
        if isinstance(dm, list):
            for entry in dm:
                if isinstance(entry, dict):
                    name = entry.get("entity") or entry.get("name") or "(unnamed entity)"
                    add(name, 3)
                    if entry.get("description"):
                        add(entry["description"])
                    fields = entry.get("fields") or entry.get("key_fields") or []
                    if isinstance(fields, list):
                        for f in fields:
                            if isinstance(f, dict):
                                fname = f.get("name") or ""
                                ftype = f.get("type") or "(NOT TYPED — re-run analysis)"
                                nullable = f.get("nullable")
                                fdesc = f.get("description") or f.get("notes") or ""
                                enum_vals = f.get("enum_values") or []
                                line = f"{fname} ({ftype}"
                                if nullable is True:
                                    line += ", nullable"
                                elif nullable is False:
                                    line += ", required"
                                line += ")"
                                if fdesc:
                                    line += f" — {fdesc}"
                                bullet(line)
                                if enum_vals:
                                    add(f"  enum: {', '.join(enum_vals)}")
                            else:
                                bullet(str(f))
                    elif isinstance(fields, str):
                        add(fields)
                    sm = entry.get("state_machine") or {}
                    if sm:
                        states = sm.get("states") or []
                        transitions = sm.get("transitions") or []
                        if states or transitions:
                            add("State machine:")
                            if states:
                                bullet(f"States: {', '.join(states)}")
                            for t in transitions:
                                if isinstance(t, dict):
                                    bullet(f"{t.get('from', '?')} → {t.get('to', '?')} on '{t.get('trigger', '?')}'")
                    if entry.get("relationships"):
                        rels = entry["relationships"]
                        rels_str = ", ".join(rels) if isinstance(rels, list) else str(rels)
                        add(f"  Relationships: {rels_str}")
                else:
                    bullet(str(entry))
        else:
            add(str(dm))
        add()

    # 4b. API Surface ─────────────────────────────────────────
    if eadd.get("api_surface"):
        add("4b. API Surface", 2)
        add("REST endpoints the dev team must implement.")
        for ep in eadd["api_surface"]:
            if not isinstance(ep, dict):
                bullet(str(ep))
                continue
            method = ep.get("method") or "?"
            path = ep.get("path") or "?"
            purpose = ep.get("purpose") or ""
            bullet(f"{method} {path} — {purpose}")
            if ep.get("auth_required"):
                add(f"  Auth: {ep['auth_required']}")
            if ep.get("request_shape"):
                add(f"  Request: {ep['request_shape']}")
            if ep.get("response_shape"):
                add(f"  Response: {ep['response_shape']}")
        add()

    # 4c. Auth Model ──────────────────────────────────────────
    auth_model = eadd.get("auth_model") or {}
    if auth_model.get("session_model") or auth_model.get("roles") or auth_model.get("ownership_model"):
        add("4c. Auth Model (app users)", 2)
        if auth_model.get("session_model"):
            add(f"Session: {auth_model['session_model']}")
        if auth_model.get("roles"):
            add("Roles:")
            for r in auth_model["roles"]:
                if isinstance(r, dict):
                    name = r.get("name") or "(unnamed)"
                    perms = r.get("permissions") or []
                    perms_str = "; ".join(perms) if isinstance(perms, list) else str(perms)
                    bullet(f"{name}: {perms_str}")
        if auth_model.get("ownership_model"):
            add(f"Ownership: {auth_model['ownership_model']}")
        add()

    # 5. Integrations & Cross-System Handoffs ─────────────────
    if eadd.get("integrations") or app.get("integrations"):
        add("5. Integrations & Cross-System Handoffs", 2)
        # eadd integrations are the design-doc level (with direction/trigger)
        for integ in eadd.get("integrations") or []:
            if isinstance(integ, dict):
                target = integ.get("target_name") or integ.get("name") or "(unnamed)"
                direction = integ.get("direction") or ""
                trigger = integ.get("trigger") or ""
                header = target
                if direction:
                    header += f" ({direction})"
                bullet(header)
                if trigger:
                    add(f"  Trigger: {trigger}")
                payload_field = integ.get("payload")
                if isinstance(payload_field, list) and payload_field:
                    add("  Payload:")
                    for pf in payload_field:
                        if isinstance(pf, dict):
                            line = f"    • {pf.get('field', '?')} ({pf.get('type', '?')})"
                            if pf.get("description"):
                                line += f" — {pf['description']}"
                            add(line)
                        else:
                            add(f"    • {pf}")
                elif payload_field:
                    add(f"  Payload: {payload_field}")
                if integ.get("auth_note"):
                    add(f"  Auth: {integ['auth_note']}")
            else:
                bullet(str(integ))
        # app.integrations is the surface-level 3-bucket inventory + the
        # build team's required per-integration depth (auth, rate limits,
        # sandbox, webhook vs polling, read/write, access status). Missing
        # fields render explicit "(NOT CAPTURED)" so devs can chase them
        # rather than silently absorbing the gap.
        DEPTH_FIELDS = [
            ("Disposition (integrate vs replace)", "disposition"),
            ("Auth method", "auth_method"),
            ("Rate limits / API tier", "rate_limits"),
            ("Sandbox available", "sandbox_availability"),
            ("Pattern (webhook / polling / batch)", "interaction_pattern"),
            ("Read / write", "read_write"),
            ("Access status", "access_status"),
        ]
        for integ in app.get("integrations") or []:
            if not isinstance(integ, dict):
                bullet(str(integ))
                continue
            sys_name = integ.get("system_name") or "(unnamed)"
            bucket = integ.get("bucket")
            bucket_label = {1: "pre-research", 2: "transcript", 3: "needs-followup"}.get(bucket, f"bucket {bucket}" if bucket else "")
            line = sys_name
            if bucket_label:
                line += f" [{bucket_label}]"
            bullet(line)
            if integ.get("notes"):
                add(f"  Notes: {integ['notes']}")
            for label, key in DEPTH_FIELDS:
                v = integ.get(key)
                add(f"  {label}: {v if v else '(NOT CAPTURED)'}")
        add()

    # 5b. Build Context (per dev requirements) ────────────────
    add("5b. Build Context", 2)
    add(
        "Consolidated tech stack, scale, and production-tier signals for "
        "estimation and build-vs-reuse decisions."
    )
    nfrs = app.get("nfrs") or {}
    scale = app.get("scale_profile") or {}

    def _fmt_list(vals):
        if not vals:
            return "(NOT CAPTURED — re-run analysis)"
        if isinstance(vals, list):
            return "; ".join(str(v) for v in vals) or "(NOT CAPTURED — re-run analysis)"
        return str(vals)

    bullet(f"Production tier — RBAC: {nfrs.get('rbac') or '(NOT CAPTURED)'}")
    bullet(f"Production tier — SLA: {nfrs.get('sla') or '(NOT CAPTURED)'}")
    bullet(f"Production tier — Compliance: {nfrs.get('compliance') or '(NOT CAPTURED)'}")
    bullet(f"Production tier — Multi-region: {nfrs.get('multi_region') or '(NOT CAPTURED)'}")
    bullet(f"Production tier — Audit logging: {nfrs.get('audit_logging') or '(NOT CAPTURED)'}")
    bullet(f"Performance budget: {nfrs.get('performance_budgets') or '(NOT CAPTURED)'}")
    bullet(f"Data volume: {scale.get('transaction_volume') or '(NOT CAPTURED)'}")
    bullet(f"Retention: {scale.get('retention_window') or '(NOT CAPTURED)'}")
    bullet(f"Growth: {scale.get('growth_trajectory') or '(NOT CAPTURED)'}")
    bullet(f"Storage tier: {scale.get('inference') or '(NOT CAPTURED)'}")
    bullet(f"Novel UI patterns: {_fmt_list(app.get('novel_ui_patterns'))}")
    bullet(f"Custom logic rules: {_fmt_list(app.get('custom_logic'))}")
    # Auto-derived sizing checks for the build team
    entity_count = len((app.get("eadd_doc") or {}).get("data_model") or [])
    integ_count = len(app.get("integrations") or [])
    flow_count = len((app.get("eadd_doc") or {}).get("core_flows") or [])
    novel_ui_count = len(app.get("novel_ui_patterns") or [])
    custom_logic_count = len(app.get("custom_logic") or [])
    bullet(
        f"Sizing snapshot: {entity_count} entities · {integ_count} integrations · "
        f"{flow_count} core flows · {novel_ui_count} novel UI patterns · "
        f"{custom_logic_count} custom logic rules"
    )
    add()

    # 6. Non-Functional Requirements ──────────────────────────
    nfrs = app.get("nfrs") or {}
    if nfrs:
        add("6. Non-Functional Requirements", 2)
        for label, key in (
            ("RBAC / Access Control", "rbac"),
            ("SLA / Uptime", "sla"),
            ("Compliance", "compliance"),
            ("Multi-region / Data Residency", "multi_region"),
            ("Data Freshness", "data_freshness"),
            ("Device Support", "device_support"),
            ("Browser Support", "browser_support"),
        ):
            v = nfrs.get(key)
            if v:
                bullet(f"{label}: {v}")
        add()

    # 7. Scale Profile ────────────────────────────────────────
    scale = app.get("scale_profile") or {}
    if scale and any(scale.get(k) for k in ("users", "concurrent_users", "throughput", "data_volume", "peak_load")):
        add("7. Scale Profile", 2)
        for label, key in (
            ("Total users", "users"),
            ("Concurrent users", "concurrent_users"),
            ("Throughput", "throughput"),
            ("Data volume", "data_volume"),
            ("Peak load", "peak_load"),
        ):
            v = scale.get(key)
            if v:
                bullet(f"{label}: {v}")
        add()

    # 8. Business Rules ───────────────────────────────────────
    if eadd.get("business_rules"):
        add("8. Business Rules", 2)
        add("IF/THEN decisions the app must enforce. Drives controller logic and tests.")
        for rule in eadd["business_rules"]:
            if isinstance(rule, dict):
                name = rule.get("name") or "(unnamed rule)"
                bullet(f"{name}: {rule.get('rule', '')}")
                if rule.get("rationale"):
                    add(f"  Rationale: {rule['rationale']}")
            else:
                bullet(str(rule))
        add()

    # 9. File Handling ────────────────────────────────────────
    fh = eadd.get("file_handling") or {}
    if fh.get("accepts") or fh.get("extraction_strategy"):
        add("9. File Handling", 2)
        if fh.get("accepts"):
            bullet(f"Accepts: {', '.join(fh['accepts'])}")
        if fh.get("extraction_strategy"):
            bullet(f"Extraction strategy: {fh['extraction_strategy']}")
        if fh.get("max_size_mb"):
            bullet(f"Max size per file: {fh['max_size_mb']} MB")
        add()

    # 10. Observability ───────────────────────────────────────
    obs = eadd.get("observability") or {}
    if obs.get("events_to_log") or obs.get("metrics_to_track") or obs.get("alerts"):
        add("10. Observability", 2)
        if obs.get("events_to_log"):
            add("Domain events to log:")
            for e in obs["events_to_log"]:
                bullet(str(e))
        if obs.get("metrics_to_track"):
            add("Metrics:")
            for m in obs["metrics_to_track"]:
                bullet(str(m))
        if obs.get("alerts"):
            add("Alerts:")
            for a in obs["alerts"]:
                bullet(str(a))
        add()

    # 11. Test Strategy ───────────────────────────────────────
    ts = eadd.get("test_strategy") or {}
    if ts:
        add("11. Test Strategy", 2)
        if ts.get("unit_focus"):
            bullet(f"Unit focus: {ts['unit_focus']}")
        if ts.get("integration_focus"):
            bullet(f"Integration focus: {ts['integration_focus']}")
        if ts.get("e2e_critical_paths"):
            add("E2E critical paths:")
            for p in ts["e2e_critical_paths"]:
                bullet(str(p))
        add()

    # 12. Out of Scope ────────────────────────────────────────
    if eadd.get("out_of_scope"):
        add("12. Out of Scope (v1)", 2)
        for item in eadd["out_of_scope"]:
            bullet(str(item))
        add()

    # 13. Visual Style ────────────────────────────────────────
    if eadd.get("visual_style"):
        add("13. Visual Style", 2)
        add(str(eadd["visual_style"]))
        add()

    # Appendix A — Sales-side ideation prompt (NOT the build spec) ─
    if eadd.get("starter_prompt"):
        add("Appendix A — Sales Ideation Prompt (not the build spec)", 2)
        add(
            "This short prompt is for sales / pre-sales to feed an AI app builder when "
            "ideating a v1 POC. It is NOT a substitute for sections 1-13 above — the dev "
            "team builds the production app from those structured sections, not from this "
            "prompt. If a section above is thin, this prompt cannot fill the gap."
        )
        add()
        add(eadd["starter_prompt"])
        add()

    return seg


# ---------------------------------------------------------------------------
# Renderers — segments → markdown / PDF
# ---------------------------------------------------------------------------


def _segments_to_markdown(segments: list[tuple[str, int]]) -> str:
    """Render segments to clean markdown.

    Heading levels become #/##/###, blank rows are preserved, and body lines
    that start with the unicode bullet (``•``) are converted to proper ``- ``
    markdown lists with two-space indentation per level so a markdown viewer
    actually renders them as nested bullets instead of leading-whitespace
    inline text.
    """
    lines: list[str] = []
    last_was_heading = False
    for text, level in segments:
        if level == 1:
            if lines:
                lines.append("")
            lines.append(f"# {text}")
            lines.append("")
            last_was_heading = True
            continue
        if level == 2:
            if lines and not last_was_heading:
                lines.append("")
            lines.append(f"## {text}")
            lines.append("")
            last_was_heading = True
            continue
        if level == 3:
            if lines and not last_was_heading:
                lines.append("")
            lines.append(f"### {text}")
            lines.append("")
            last_was_heading = True
            continue
        # Body line — convert "  • foo" into "  - foo" (markdown bullet)
        last_was_heading = False
        body = text or ""
        stripped = body.lstrip(" ")
        indent_spaces = len(body) - len(stripped)
        if stripped.startswith("• "):
            lines.append(" " * indent_spaces + "- " + stripped[2:])
        else:
            lines.append(body)
    return "\n".join(lines).rstrip() + "\n"


def _segments_to_pdf(segments: list[tuple[str, int]]) -> Optional[bytes]:
    """Render segments to a styled PDF that matches the AEGISAXIOM visual identity.

    Uses violet section bars, hanging-indent bullets, and a footer with the
    page number + the Vega / AEGISAXIOM attribution so the document looks like an
    actual deliverable instead of a wall of unstyled text.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError:
        return None

    BRAND = colors.HexColor("#6d28d9")           # violet-700
    BRAND_LIGHT = colors.HexColor("#a78bfa")     # violet-400
    INK = colors.HexColor("#1f2937")             # slate-800
    SUBTLE = colors.HexColor("#64748b")          # slate-500
    DIVIDER = colors.HexColor("#e5e7eb")         # gray-200

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "DocTitle", parent=styles["Title"],
        fontName="Helvetica-Bold", fontSize=22,
        textColor=BRAND, spaceAfter=2, leading=26, alignment=TA_LEFT,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle", parent=styles["Normal"],
        fontName="Helvetica", fontSize=10,
        textColor=SUBTLE, spaceAfter=12, leading=12,
    )
    h2_style = ParagraphStyle(
        "H2", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=14,
        textColor=BRAND, spaceBefore=14, spaceAfter=6, leading=18,
    )
    h3_style = ParagraphStyle(
        "H3", parent=styles["Heading3"],
        fontName="Helvetica-Bold", fontSize=11,
        textColor=INK, spaceBefore=8, spaceAfter=2, leading=14,
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["BodyText"],
        fontName="Helvetica", fontSize=10,
        textColor=INK, leading=14, spaceAfter=4, alignment=TA_JUSTIFY,
    )
    bullet_style = ParagraphStyle(
        "Bullet", parent=body_style,
        leftIndent=18, bulletIndent=6, spaceAfter=2, alignment=TA_LEFT,
    )
    sub_bullet_style = ParagraphStyle(
        "SubBullet", parent=body_style,
        leftIndent=36, bulletIndent=24, spaceAfter=2, alignment=TA_LEFT,
        textColor=colors.HexColor("#374151"),
    )
    code_style = ParagraphStyle(
        "Code", parent=body_style,
        fontName="Courier", fontSize=9,
        textColor=INK, leftIndent=6, rightIndent=6,
        backColor=colors.HexColor("#f8fafc"),
        borderColor=DIVIDER, borderWidth=0.5, borderPadding=8,
        spaceBefore=4, spaceAfter=8, leading=12, alignment=TA_LEFT,
    )

    def _h2_block(text: str):
        # Violet bar + section heading inside a one-row, two-column table so
        # the heading sits next to a colored stripe like the v2 design docs.
        bar = Table(
            [[""]], colWidths=[3], rowHeights=[18],
        )
        bar.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BRAND),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        wrapper = Table(
            [[bar, Paragraph(text, h2_style)]],
            colWidths=[6, None],
        )
        wrapper.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return wrapper

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter, title="Vega Discovery",
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.7 * inch, bottomMargin=0.85 * inch,
    )

    def _on_page(canvas, _doc):
        canvas.saveState()
        # Top accent rule
        canvas.setStrokeColor(BRAND_LIGHT)
        canvas.setLineWidth(2)
        canvas.line(0.7 * inch, letter[1] - 0.55 * inch,
                    letter[0] - 0.7 * inch, letter[1] - 0.55 * inch)
        # Footer
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(SUBTLE)
        canvas.drawString(0.7 * inch, 0.5 * inch, "Vega — AI Recruiter Agent · AEGISAXIOM")
        canvas.drawRightString(letter[0] - 0.7 * inch, 0.5 * inch,
                               f"Page {_doc.page}")
        canvas.restoreState()

    flow = []
    in_appendix_prompt = False  # the starter prompt body uses code style

    for text, level in segments:
        raw = text or ""
        safe = raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        if level == 1:
            flow.append(Paragraph(safe, title_style))
            continue
        if level == 2:
            flow.append(Spacer(1, 4))
            flow.append(_h2_block(safe))
            in_appendix_prompt = "starter prompt" in raw.lower() or "appendix a" in raw.lower()
            continue
        if level == 3:
            flow.append(Paragraph(safe, h3_style))
            continue
        if not raw.strip():
            flow.append(Spacer(1, 4))
            continue

        stripped = raw.lstrip(" ")
        indent_spaces = len(raw) - len(stripped)
        if stripped.startswith("• "):
            text_only = stripped[2:].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            style = sub_bullet_style if indent_spaces >= 4 else bullet_style
            flow.append(Paragraph(text_only, style, bulletText="•"))
            continue

        # Long monospace blocks (starter prompt body) get the boxed code style
        if in_appendix_prompt and len(raw) > 200 and not raw.startswith("The block below"):
            # Preserve newlines for code block
            html = safe.replace("\n", "<br/>")
            flow.append(Paragraph(html, code_style))
            in_appendix_prompt = False
            continue

        # Indented sub-detail (e.g., "  Trigger: foo") gets a softer, indented body
        if indent_spaces >= 2:
            sub_style = ParagraphStyle(
                "SubBody", parent=body_style,
                leftIndent=18, textColor=colors.HexColor("#475569"),
                spaceAfter=2, alignment=TA_LEFT,
            )
            flow.append(Paragraph(stripped.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), sub_style))
            continue

        # Plain body — "Client: X / Generated: Y" right under the title get the subtitle style
        if raw.startswith(("Client:", "Generated:")):
            flow.append(Paragraph(safe, subtitle_style))
            continue

        flow.append(Paragraph(safe, body_style))

    doc.build(flow, onFirstPage=_on_page, onLaterPages=_on_page)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _build_segments(payload: dict, target: str, entity_id: Optional[str]) -> list[tuple[str, int]]:
    if target == "analysis":
        return _segments_for_analysis(payload)
    if target == "design_doc":
        if not entity_id:
            raise HTTPException(status_code=400, detail="entity_id (workflow_id) required for design_doc")
        return _segments_for_design_doc(payload, entity_id)
    if target == "eadd":
        if not entity_id:
            raise HTTPException(status_code=400, detail="entity_id (app_id) required for eadd")
        return _segments_for_eadd(payload, entity_id)
    raise HTTPException(status_code=400, detail=f"Unknown export target: {target!r}")


def _slugify(text: str, fallback: str = "vega") -> str:
    """Lower-snake-case + filesystem-safe. Falls back when text is empty."""
    if not text:
        return fallback
    keep = []
    for ch in text.strip().lower():
        if ch.isalnum():
            keep.append(ch)
        elif ch in (" ", "-", "_", "/", "."):
            keep.append("_")
    slug = "".join(keep).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug[:60] or fallback


def _filename_for(payload: dict, target: str, entity_id: Optional[str], ext: str) -> str:
    """Build a human-readable export filename including the client + entity."""
    client_slug = _slugify(payload.get("client_company") or "", "client")
    if target == "analysis":
        return f"vega_discovery_{client_slug}.{ext}"
    if target == "design_doc":
        wf = next(
            (w for w in (payload.get("workflows") or []) if w.get("workflow_id") == entity_id),
            None,
        )
        wf_slug = _slugify((wf or {}).get("workflow_name") or entity_id or "", "workflow")
        return f"ai_design_doc_{client_slug}_{wf_slug}.{ext}"
    if target == "eadd":
        app = next(
            (a for a in (payload.get("apps") or []) if a.get("app_id") == entity_id),
            None,
        )
        app_slug = _slugify((app or {}).get("app_name") or entity_id or "", "app")
        return f"app_design_doc_{client_slug}_{app_slug}.{ext}"
    return f"vega_export.{ext}"


@router.get("/vega-artifacts/{artifact_id}/export")
async def export_v3_artifact(
    artifact_id: str,
    target: str = Query("analysis", pattern="^(analysis|design_doc|eadd)$"),
    entity_id: Optional[str] = Query(None),
    format: str = Query("markdown", pattern="^(markdown|pdf|json)$"),
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "vega.use"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    art = await _load_v3_artifact(db, artifact_id, current_user.user_id)
    payload = art.get("payload") or {}

    if format == "json":
        if target == "analysis":
            return payload
        if target == "design_doc":
            wf = next((w for w in (payload.get("workflows") or []) if w.get("workflow_id") == entity_id), None)
            if not wf:
                raise HTTPException(status_code=404, detail="Workflow not found")
            return wf.get("design_doc") or {}
        if target == "eadd":
            app = next((a for a in (payload.get("apps") or []) if a.get("app_id") == entity_id), None)
            if not app:
                raise HTTPException(status_code=404, detail="App not found")
            return app.get("eadd_doc") or {}

    segments = _build_segments(payload, target, entity_id)

    await emit_vega_event(
        db, current_user.user_id, art.get("session_id") or "", "v3_export_triggered",
        {"artifact_id": artifact_id, "target": target, "format": format, "entity_id": entity_id},
    )

    if format == "markdown":
        md = _segments_to_markdown(segments)
        filename = _filename_for(payload, target, entity_id, "md")
        return Response(
            content=md, media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    pdf_bytes = _segments_to_pdf(segments)
    if pdf_bytes is None:
        # Fallback: return markdown with a 200 + a header noting the fallback
        md = _segments_to_markdown(segments)
        filename = _filename_for(payload, target, entity_id, "md")
        return Response(
            content=md, media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Export-Fallback": "reportlab-missing",
            },
        )
    filename = _filename_for(payload, target, entity_id, "pdf")
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/vega-artifacts/{artifact_id}/export/google-doc")
async def export_v3_to_google_doc(
    artifact_id: str,
    target: str = Query("analysis", pattern="^(analysis|design_doc|eadd)$"),
    entity_id: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "vega.use"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    art = await _load_v3_artifact(db, artifact_id, current_user.user_id)
    payload = art.get("payload") or {}
    segments = _build_segments(payload, target, entity_id)

    try:
        from services.google_docs_service import (
            DRIVE_FOLDER_ID,
            _get_credentials,
            _get_user_credentials,
        )
        from googleapiclient.discovery import build as google_build
    except ImportError:
        raise HTTPException(status_code=501, detail="Google Docs integration is not configured.")

    try:
        creds = await _get_user_credentials(db, current_user.user_id)
    except FileNotFoundError:
        try:
            creds = _get_credentials()
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Google Docs is not connected. Please sign out and sign back in "
                    f"with Google to authorize. ({str(exc)[:160]})"
                ),
            )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Google credentials error: {str(exc)[:200]}")

    HEADING_MAP = {1: "HEADING_1", 2: "HEADING_2", 3: "HEADING_3"}

    full_text = ""
    heading_ranges: list[tuple[int, int, int]] = []
    cur = 1
    for text, level in segments:
        line = (text or "") + "\n"
        if level in HEADING_MAP and text:
            heading_ranges.append((cur, cur + len(text), level))
        cur += len(line)
        full_text += line

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    client = payload.get("client_company") or "Unknown"
    if target == "analysis":
        doc_title = f"{client} — Vega Discovery Report — {today}"
    elif target == "design_doc":
        wf = next((w for w in (payload.get("workflows") or []) if w.get("workflow_id") == entity_id), {})
        doc_title = f"{client} — Design Doc: {wf.get('workflow_name', entity_id)} — {today}"
    else:
        app = next((a for a in (payload.get("apps") or []) if a.get("app_id") == entity_id), {})
        doc_title = f"{client} — App Design: {app.get('app_name', entity_id)} — {today}"

    try:
        docs_service = google_build("docs", "v1", credentials=creds)
        drive_service = google_build("drive", "v3", credentials=creds)

        created_doc = await asyncio.to_thread(
            docs_service.documents().create(body={"title": doc_title}).execute
        )
        doc_id = created_doc["documentId"]
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

        requests = [{"insertText": {"location": {"index": 1}, "text": full_text}}]
        for start, end, level in heading_ranges:
            requests.append({
                "updateParagraphStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "paragraphStyle": {"namedStyleType": HEADING_MAP[level]},
                    "fields": "namedStyleType",
                }
            })

        await asyncio.to_thread(
            docs_service.documents()
            .batchUpdate(documentId=doc_id, body={"requests": requests})
            .execute
        )

        if DRIVE_FOLDER_ID:
            try:
                await asyncio.to_thread(
                    drive_service.files()
                    .update(fileId=doc_id, addParents=DRIVE_FOLDER_ID, fields="id,parents")
                    .execute
                )
            except Exception as exc:
                logger.warning("Failed to move doc to Drive folder: %s", exc)

        if hasattr(current_user, "email") and current_user.email:
            try:
                await asyncio.to_thread(
                    drive_service.permissions()
                    .create(
                        fileId=doc_id,
                        body={"type": "user", "role": "writer", "emailAddress": current_user.email},
                        sendNotificationEmail=False,
                    )
                    .execute
                )
            except Exception as exc:
                logger.warning("Failed to share doc with %s: %s", current_user.email, exc)

        await emit_vega_event(
            db, current_user.user_id, art.get("session_id") or "", "v3_export_triggered",
            {"artifact_id": artifact_id, "target": target, "format": "google_doc", "entity_id": entity_id, "doc_id": doc_id},
        )
        return {"doc_url": doc_url, "doc_id": doc_id, "title": doc_title}
    except Exception as exc:
        logger.exception("v3 google doc export failed")
        raise HTTPException(status_code=500, detail=f"Failed to create Google Doc: {str(exc)[:200]}")
