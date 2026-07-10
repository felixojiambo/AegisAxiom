"""Deterministic JSON -> semantic BPMN 2.0 XML converter.

Takes validated workflow JSON (conforming to WorkflowSchema) and produces
BPMN 2.0 XML with collaboration, lanes, tasks, gateways, boundary events,
sequence flows, named data objects, and call activities. Does NOT include
bpmndi:BPMNDiagram — that is added by the auto-layout step.
"""

import re
from xml.sax.saxutils import escape as xml_escape
from typing import Dict, List, Set, Tuple

from services.bpmn.schema import WorkflowSchema, BpmnDataObject


# ---------------------------------------------------------------------------
# BPMN element-type mappings
# ---------------------------------------------------------------------------

_TASK_TYPE_MAP = {
    "user": "bpmn:userTask",
    "service": "bpmn:serviceTask",
    "business_rule": "bpmn:businessRuleTask",
    "manual": "bpmn:manualTask",
}

_GATEWAY_TYPE_MAP = {
    "exclusive": "bpmn:exclusiveGateway",
    "parallel": "bpmn:parallelGateway",
    "inclusive": "bpmn:inclusiveGateway",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    """XML-escape user-provided text for safe embedding in attributes / content."""
    return xml_escape(text, entities={'"': "&quot;", "'": "&apos;"})


def _make_flow_id(src: str, tgt: str) -> str:
    return f"Flow_{src}_{tgt}"


def _slug(name: str) -> str:
    """Stable lowercase ID slug for a data-object name."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).strip("_").lower()
    return s or "data"


def _derive_data_objects(wf: WorkflowSchema) -> Tuple[List[BpmnDataObject], Dict[str, List[str]], Dict[str, List[str]]]:
    """If WorkflowSchema.data_objects is empty, auto-derive from per-step
    data_inputs/data_outputs. Returns (data_objects, by_consumer_step,
    by_producer_step) where the dicts are step_id -> list of data_object_ids
    so the renderer can wire associations inside each task.

    Only named data is rendered — generic stuff like "input" is skipped.
    Multiple steps consuming/producing the same name share one data object.
    """
    explicit = list(wf.data_objects or [])
    if explicit:
        # If the LLM emitted explicit objects, trust them but still build the
        # by_consumer / by_producer maps so the renderer can wire associations.
        by_consumer: Dict[str, List[str]] = {}
        by_producer: Dict[str, List[str]] = {}
        for obj in explicit:
            if obj.target_step_id:
                by_consumer.setdefault(obj.target_step_id, []).append(obj.id)
            if obj.source_step_id:
                by_producer.setdefault(obj.source_step_id, []).append(obj.id)
        return explicit, by_consumer, by_producer

    # Auto-derive: scan each step's data_inputs / data_outputs by NAME.
    # Skip names that are obviously generic placeholders.
    GENERIC = {"data", "input", "output", "info", "the data", "raw data", "data1"}

    derived: Dict[str, BpmnDataObject] = {}
    by_consumer: Dict[str, List[str]] = {}
    by_producer: Dict[str, List[str]] = {}

    def _register(name: str, *, producer: str = None, consumer: str = None, fmt: str = None):
        clean = (name or "").strip()
        if not clean or clean.lower() in GENERIC:
            return
        slug = _slug(clean)
        obj = derived.get(slug)
        if obj is None:
            obj = BpmnDataObject(
                id=f"DataObj_{slug}", name=clean,
                source_step_id=producer, target_step_id=consumer, format=fmt,
            )
            derived[slug] = obj
        else:
            # Strengthen producer/consumer/format if previously missing
            if producer and not obj.source_step_id:
                obj.source_step_id = producer
            if consumer and not obj.target_step_id:
                obj.target_step_id = consumer
            if fmt and not obj.format:
                obj.format = fmt
        if consumer:
            by_consumer.setdefault(consumer, []).append(obj.id)
        if producer:
            by_producer.setdefault(producer, []).append(obj.id)

    for step in wf.steps:
        for inp in step.data_inputs or []:
            _register(inp.name, consumer=step.id, fmt=inp.format)
        for out in step.data_outputs or []:
            _register(out.name, producer=step.id, fmt=out.format)

    return list(derived.values()), by_consumer, by_producer


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def json_to_semantic_bpmn(workflow_json: dict) -> str:
    """Generate semantic BPMN 2.0 XML (without DI layout) from workflow JSON.

    Creates a collaboration with one pool containing lanes for each actor.
    Steps become tasks, decision points become gateways, exception paths
    become boundary events.
    """
    wf = WorkflowSchema(**workflow_json)

    wf_id = wf.workflow_id
    wf_name = _esc(wf.workflow_name)

    # Build actor lookup
    actor_map: Dict[str, str] = {a.id: a.name for a in wf.actors}

    # Track which flow-node IDs belong to each actor (for lane refs)
    lane_refs: Dict[str, List[str]] = {a.id: [] for a in wf.actors}

    # Accumulators for XML fragments
    elements: List[str] = []
    flows: List[str] = []
    # ordered node ids for building the main sequence
    main_sequence: List[str] = []

    # ------------------------------------------------------------------
    # Start event
    # ------------------------------------------------------------------
    start_id = f"StartEvent_{wf_id}"
    trigger_type = wf.trigger.type
    if trigger_type == "scheduled":
        elements.append(
            f'    <bpmn:startEvent id="{start_id}" name="Start ({_esc(wf.trigger.description or "Scheduled")})">'
            f"\n      <bpmn:timerEventDefinition />"
            f"\n    </bpmn:startEvent>"
        )
    elif trigger_type == "event":
        elements.append(
            f'    <bpmn:startEvent id="{start_id}" name="Start ({_esc(wf.trigger.description or "Event")})">'
            f"\n      <bpmn:messageEventDefinition />"
            f"\n    </bpmn:startEvent>"
        )
    else:
        # manual or api_call -> plain start event
        label = wf.trigger.description or trigger_type.replace("_", " ").title()
        elements.append(
            f'    <bpmn:startEvent id="{start_id}" name="Start ({_esc(label)})" />'
        )

    # Assign start event to trigger actor's lane
    if wf.trigger.actor_id in lane_refs:
        lane_refs[wf.trigger.actor_id].append(start_id)

    main_sequence.append(start_id)

    # ------------------------------------------------------------------
    # Build lookup: decision points keyed by after_step_id
    # ------------------------------------------------------------------
    dp_after: Dict[str, list] = {}
    for dp in wf.decision_points:
        dp_after.setdefault(dp.after_step_id, []).append(dp)

    # Build lookup: outputs keyed by after_step_id
    out_after: Dict[str, list] = {}
    for out in wf.outputs:
        out_after.setdefault(out.after_step_id, []).append(out)

    # Build lookup: exception paths keyed by from_step_id
    exc_from: Dict[str, list] = {}
    for ep in wf.exception_paths:
        exc_from.setdefault(ep.from_step_id, []).append(ep)

    # ------------------------------------------------------------------
    # Data objects — derived from per-step data_inputs/data_outputs unless
    # WorkflowSchema.data_objects was explicitly populated.
    # ------------------------------------------------------------------
    data_objects, consumers_by_step, producers_by_step = _derive_data_objects(wf)

    # ------------------------------------------------------------------
    # Tasks (steps)
    # ------------------------------------------------------------------
    for step in wf.steps:
        task_tag = _TASK_TYPE_MAP.get(step.task_type, "bpmn:task")
        task_id = f"Activity_{step.id}"
        name_attr = f' name="{_esc(step.name)}"'

        consumed = consumers_by_step.get(step.id, [])
        produced = producers_by_step.get(step.id, [])

        if not consumed and not produced:
            # No associations — keep self-closing for compactness
            elements.append(f"    <{task_tag} id=\"{task_id}\"{name_attr} />")
        else:
            inner: List[str] = []
            for obj_id in consumed:
                ref_id = f"DataRef_{obj_id.removeprefix('DataObj_')}"
                inner.append(
                    f"      <bpmn:dataInputAssociation id=\"DataInAssoc_{step.id}_{obj_id}\">"
                    f"\n        <bpmn:sourceRef>{ref_id}</bpmn:sourceRef>"
                    f"\n        <bpmn:targetRef>DataInput_{step.id}_{obj_id}</bpmn:targetRef>"
                    f"\n      </bpmn:dataInputAssociation>"
                )
            for obj_id in produced:
                ref_id = f"DataRef_{obj_id.removeprefix('DataObj_')}"
                inner.append(
                    f"      <bpmn:dataOutputAssociation id=\"DataOutAssoc_{step.id}_{obj_id}\">"
                    f"\n        <bpmn:targetRef>{ref_id}</bpmn:targetRef>"
                    f"\n      </bpmn:dataOutputAssociation>"
                )
            inner_block = "\n".join(inner)
            elements.append(
                f"    <{task_tag} id=\"{task_id}\"{name_attr}>\n{inner_block}\n    </{task_tag}>"
            )

        if step.actor_id in lane_refs:
            lane_refs[step.actor_id].append(task_id)

        main_sequence.append(task_id)

    # ------------------------------------------------------------------
    # Call activities — reusable subroutines (Camunda BPMN 2.0)
    # ------------------------------------------------------------------
    for ca in wf.call_activities:
        ca_id = f"CallActivity_{ca.id}"
        elements.append(
            f'    <bpmn:callActivity id="{ca_id}" name="{_esc(ca.name)}" '
            f'calledElement="{_esc(ca.called_element_ref)}" />'
        )
        # Place in the AI lane if one exists, else the first lane
        ai_actor = next((a for a in wf.actors if a.type == "ai"), None)
        target_actor = ai_actor or (wf.actors[0] if wf.actors else None)
        if target_actor and target_actor.id in lane_refs:
            lane_refs[target_actor.id].append(ca_id)

    # ------------------------------------------------------------------
    # Decision-point gateways
    # ------------------------------------------------------------------
    gateway_elements: List[str] = []
    # We need to track which gateway branches we must wire after the
    # main sequence is built, so we collect them as deferred flow defs.
    deferred_gateway_flows: List[tuple] = []  # (src_id, tgt_id)

    for dp in wf.decision_points:
        gw_tag = _GATEWAY_TYPE_MAP.get(dp.type, "bpmn:exclusiveGateway")
        gw_id = f"Gateway_{dp.id}"
        gw_name = f' name="{_esc(dp.name)}"' if dp.name else ""
        gateway_elements.append(f"    <{gw_tag} id=\"{gw_id}\"{gw_name} />")

        # Place gateway in same lane as the step it follows
        after_step = next((s for s in wf.steps if s.id == dp.after_step_id), None)
        if after_step and after_step.actor_id in lane_refs:
            lane_refs[after_step.actor_id].append(gw_id)

        # Wire: after_step -> gateway
        src_task_id = f"Activity_{dp.after_step_id}"
        deferred_gateway_flows.append((src_task_id, gw_id))

        # Wire: gateway -> each branch target
        for br in dp.branches:
            if br.leads_to_step_id == "end":
                # Create an end event for this branch
                end_id = f"EndEvent_{dp.id}_{br.leads_to_step_id}"
                elements.append(
                    f'    <bpmn:endEvent id="{end_id}" name="End ({_esc(br.condition)})" />'
                )
                # Put it in same lane as gateway
                if after_step and after_step.actor_id in lane_refs:
                    lane_refs[after_step.actor_id].append(end_id)
                deferred_gateway_flows.append((gw_id, end_id))
            else:
                tgt_task_id = f"Activity_{br.leads_to_step_id}"
                deferred_gateway_flows.append((gw_id, tgt_task_id))

    elements.extend(gateway_elements)

    # ------------------------------------------------------------------
    # Boundary events (exception paths)
    # ------------------------------------------------------------------
    boundary_elements: List[str] = []
    boundary_flows: List[tuple] = []

    for ep in wf.exception_paths:
        boundary_id = f"BoundaryEvent_{ep.id}"
        attached_to = f"Activity_{ep.from_step_id}"
        boundary_elements.append(
            f'    <bpmn:boundaryEvent id="{boundary_id}" '
            f'name="{_esc(ep.trigger)}" '
            f'attachedToRef="{attached_to}" cancelActivity="false">'
            f"\n      <bpmn:errorEventDefinition />"
            f"\n    </bpmn:boundaryEvent>"
        )
        # Place in same lane as the attached task
        from_step = next((s for s in wf.steps if s.id == ep.from_step_id), None)
        if from_step and from_step.actor_id in lane_refs:
            lane_refs[from_step.actor_id].append(boundary_id)

        if ep.leads_to_step_id:
            if ep.leads_to_step_id == "end":
                end_id = f"EndEvent_exc_{ep.id}"
                elements.append(
                    f'    <bpmn:endEvent id="{end_id}" name="End ({_esc(ep.trigger)})" />'
                )
                if from_step and from_step.actor_id in lane_refs:
                    lane_refs[from_step.actor_id].append(end_id)
                boundary_flows.append((boundary_id, end_id))
            else:
                tgt_id = f"Activity_{ep.leads_to_step_id}"
                boundary_flows.append((boundary_id, tgt_id))

    elements.extend(boundary_elements)

    # ------------------------------------------------------------------
    # Output end events
    # ------------------------------------------------------------------
    output_flows: List[tuple] = []
    for out in wf.outputs:
        end_id = f"EndEvent_{out.id}"
        elements.append(
            f'    <bpmn:endEvent id="{end_id}" name="{_esc(out.name)}" />'
        )
        src_id = f"Activity_{out.after_step_id}"
        output_flows.append((src_id, end_id))

        # Place in same lane as the step it follows
        after_step = next((s for s in wf.steps if s.id == out.after_step_id), None)
        if after_step and after_step.actor_id in lane_refs:
            lane_refs[after_step.actor_id].append(end_id)

    # ------------------------------------------------------------------
    # Default end event (if no outputs and no decision branch ends)
    # ------------------------------------------------------------------
    has_explicit_end = bool(wf.outputs) or any(
        br.leads_to_step_id == "end"
        for dp in wf.decision_points
        for br in dp.branches
    )
    if not has_explicit_end and wf.steps:
        default_end_id = f"EndEvent_{wf_id}"
        elements.append(
            f'    <bpmn:endEvent id="{default_end_id}" name="End" />'
        )
        last_step = wf.steps[-1]
        if last_step.actor_id in lane_refs:
            lane_refs[last_step.actor_id].append(default_end_id)
        main_sequence.append(default_end_id)

    # ------------------------------------------------------------------
    # Build sequence flows
    # ------------------------------------------------------------------
    # Track which steps have gateway-supplied incoming flows so we can
    # skip the default main-sequence flow into them.
    steps_with_gateway_incoming: Set[str] = set()
    for dp in wf.decision_points:
        for br in dp.branches:
            if br.leads_to_step_id != "end":
                steps_with_gateway_incoming.add(f"Activity_{br.leads_to_step_id}")

    # Track which steps have a decision gateway attached, so we skip the
    # default main-sequence flow OUT of them (the gateway handles it).
    steps_with_gateway_outgoing: Set[str] = set()
    for dp in wf.decision_points:
        steps_with_gateway_outgoing.add(f"Activity_{dp.after_step_id}")

    # Main sequence flows (start -> step1 -> step2 -> ... -> end)
    for i in range(len(main_sequence) - 1):
        src = main_sequence[i]
        tgt = main_sequence[i + 1]
        # Skip if gateway handles this outgoing edge
        if src in steps_with_gateway_outgoing:
            continue
        # Skip if gateway supplies the incoming edge
        if tgt in steps_with_gateway_incoming:
            continue
        flow_id = _make_flow_id(src, tgt)
        flows.append(f'    <bpmn:sequenceFlow id="{flow_id}" sourceRef="{src}" targetRef="{tgt}" />')

    # Gateway flows
    for src, tgt in deferred_gateway_flows:
        flow_id = _make_flow_id(src, tgt)
        flows.append(f'    <bpmn:sequenceFlow id="{flow_id}" sourceRef="{src}" targetRef="{tgt}" />')

    # Boundary event flows
    for src, tgt in boundary_flows:
        flow_id = _make_flow_id(src, tgt)
        flows.append(f'    <bpmn:sequenceFlow id="{flow_id}" sourceRef="{src}" targetRef="{tgt}" />')

    # Output flows
    for src, tgt in output_flows:
        flow_id = _make_flow_id(src, tgt)
        flows.append(f'    <bpmn:sequenceFlow id="{flow_id}" sourceRef="{src}" targetRef="{tgt}" />')

    # ------------------------------------------------------------------
    # Data object elements (process level)
    # ------------------------------------------------------------------
    for obj in data_objects:
        slug = obj.id.removeprefix("DataObj_")
        ref_id = f"DataRef_{slug}"
        elements.append(f'    <bpmn:dataObject id="{obj.id}" />')
        elements.append(
            f'    <bpmn:dataObjectReference id="{ref_id}" '
            f'name="{_esc(obj.name)}" dataObjectRef="{obj.id}" />'
        )
        # Drop the dataObjectReference in the lane of whichever step touches it
        # so bpmn.io renders it inside the swimlane rather than floating.
        anchor_step_id = obj.source_step_id or obj.target_step_id
        if anchor_step_id:
            anchor_step = next((s for s in wf.steps if s.id == anchor_step_id), None)
            if anchor_step and anchor_step.actor_id in lane_refs:
                lane_refs[anchor_step.actor_id].append(ref_id)

    # ------------------------------------------------------------------
    # Build lanes
    # ------------------------------------------------------------------
    lane_xml_parts: List[str] = []
    for actor in wf.actors:
        refs = lane_refs.get(actor.id, [])
        if not refs:
            # Skip declared-but-unused actors so they don't render as empty
            # swim lanes in bpmn.io. The matrix may declare an actor that
            # never owns a step (e.g., LLM hallucinated a stakeholder); we
            # only want lanes for actors with at least one assigned node.
            continue
        ref_lines = "\n".join(f"        <bpmn:flowNodeRef>{r}</bpmn:flowNodeRef>" for r in refs)
        lane_xml_parts.append(
            f'      <bpmn:lane id="Lane_{actor.id}" name="{_esc(actor.name)}">\n'
            f"{ref_lines}\n"
            f"      </bpmn:lane>"
        )

    # ------------------------------------------------------------------
    # Assemble final XML
    # ------------------------------------------------------------------
    lanes_block = "\n".join(lane_xml_parts)
    elements_block = "\n".join(elements)
    flows_block = "\n".join(flows)

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  id="Definitions_{wf_id}"
                  targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:collaboration id="Collaboration_{wf_id}">
    <bpmn:participant id="Participant_{wf_id}" name="{wf_name}" processRef="Process_{wf_id}" />
  </bpmn:collaboration>
  <bpmn:process id="Process_{wf_id}" isExecutable="false">
    <bpmn:laneSet id="LaneSet_{wf_id}">
{lanes_block}
    </bpmn:laneSet>
{elements_block}
{flows_block}
  </bpmn:process>
</bpmn:definitions>"""

    return xml
