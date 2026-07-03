"""Orion SOW Builder — Vega session input adapter.

Converts Vega discovery session data into the structured text block
that Orion's SOW prompts consume as combined_context. The output format
matches what the existing SOW prompts expect from transcript-based input,
but structured and verified by Vega's analysis.
"""


def serialize_vega_session_for_prompts(vega_session: dict) -> str:
    """Convert an Vega discovery session into the structured text block
    that Orion's SOW prompts consume as combined_context."""
    parts = []

    # Header
    parts.append("=== Discovery Summary ===")
    parts.append(f"Client: {vega_session.get('client_company', 'Unknown')} - {vega_session.get('opportunity_name', '')}")
    parts.append(f"Discovery completed: {vega_session.get('updated_at', 'N/A')}")
    raw_calls = vega_session.get("calls", {})
    # Normalize: calls may be a dict keyed by call number or a list
    if isinstance(raw_calls, dict):
        calls_list = [{"call_number": int(k), **v} for k, v in raw_calls.items() if isinstance(v, dict)]
    else:
        calls_list = raw_calls if isinstance(raw_calls, list) else []
    parts.append(f"Calls analyzed: {len(calls_list)}")
    parts.append("")

    # Pain points from Call 1
    call1 = next((c for c in calls_list if c.get("call_number") == 1), None)
    if call1 and call1.get("analysis"):
        analysis = call1["analysis"]
        pain_points = analysis.get("identified_pain_points", [])
        if pain_points:
            parts.append("=== Verified Pain Points ===")
            for i, pp in enumerate(pain_points, 1):
                if isinstance(pp, dict):
                    parts.append(f"{i}. {pp.get('description', pp.get('text', str(pp)))}")
                else:
                    parts.append(f"{i}. {pp}")
            parts.append("")

        stakeholders = analysis.get("stakeholders_mentioned", [])
        if stakeholders:
            parts.append("=== Stakeholders ===")
            for sh in stakeholders:
                if isinstance(sh, dict):
                    name = sh.get("name", "Unknown")
                    role = sh.get("role", "")
                    concerns = sh.get("concerns", "")
                    line = f"- {name}"
                    if role:
                        line += f", {role}"
                    if concerns:
                        line += f": {concerns}"
                    parts.append(line)
                else:
                    parts.append(f"- {sh}")
            parts.append("")

    # Workflow inventory
    matrix = vega_session.get("ai_employee_matrix", [])
    if matrix:
        parts.append("=== Workflow Inventory ===")
        parts.append("")
        for idx, wf in enumerate(matrix, 1):
            parts.append(f"WORKFLOW {idx}: {wf.get('workflow_name', 'Unnamed')}")
            if wf.get("description"):
                parts.append(f"Description: {wf['description']}")
            if wf.get("source_apps"):
                parts.append(f"Source apps: {', '.join(wf['source_apps'])}")
            if wf.get("data_state"):
                parts.append(f"Data state: {wf['data_state']}")
            if wf.get("manual_hours_per_week") is not None:
                parts.append(f"Manual hours per week: {wf['manual_hours_per_week']}")
            if wf.get("stakeholders"):
                parts.append(f"Stakeholders: {', '.join(wf['stakeholders'])}")

            trigger = wf.get("trigger")
            if trigger and isinstance(trigger, dict):
                parts.append(f"Trigger: {trigger.get('description', trigger.get('type', 'Unknown'))} ({trigger.get('type', '')})")

            steps = wf.get("steps", [])
            if steps:
                parts.append("Steps:")
                for s in steps:
                    if isinstance(s, dict):
                        parts.append(f"  {s.get('id', '?')}. {s.get('name', 'Unnamed')}")
                    else:
                        parts.append(f"  - {s}")

            decision_points = wf.get("decision_points", [])
            if decision_points:
                parts.append("Decision points:")
                for dp in decision_points:
                    if isinstance(dp, dict):
                        parts.append(f"  - \"{dp.get('name', 'Unnamed')}\"")
                        for br in dp.get("branches", []):
                            if isinstance(br, dict):
                                parts.append(f"    → {br.get('condition', '?')} → {br.get('leads_to_step_id', '?')}")

            exception_paths = wf.get("exception_paths", [])
            if exception_paths:
                parts.append("Exception paths:")
                for ep in exception_paths:
                    if isinstance(ep, dict):
                        parts.append(f"  - {ep.get('trigger', 'Unknown')}: flagged for manual review")

            # MMI scores
            mmi = wf.get("mmi_hypothesis")
            if mmi and isinstance(mmi, dict):
                parts.append("MMI Scores (final):")
                for dim in ["tss", "ie", "time", "cost", "quality", "satisfaction"]:
                    dim_data = mmi.get(dim)
                    if isinstance(dim_data, dict):
                        val = dim_data.get("value", "?")
                        conf = dim_data.get("confidence", "?")
                        reasoning = dim_data.get("reasoning", "")
                        needs_input = dim in mmi.get("needs_client_input", [])
                        line = f"  - {dim.upper()}: {val} ({conf} confidence)"
                        if reasoning:
                            line += f" — {reasoning}"
                        if needs_input:
                            line += " — NEEDS CLIENT INPUT"
                        parts.append(line)
                if mmi.get("score") is not None:
                    parts.append(f"  - Final score: {mmi['score']}")

            parts.append("")

    # Gap notes
    gaps = vega_session.get("gap_questions", [])
    if gaps:
        parts.append("=== Gap Notes ===")
        for g in gaps:
            if isinstance(g, dict):
                parts.append(f"- {g.get('topic', 'Unknown')}: {g.get('what_we_need_to_ask', '')}")
            else:
                parts.append(f"- {g}")
        parts.append("")

    return "\n".join(parts)
