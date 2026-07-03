from pydantic import BaseModel, Field
from typing import List, Optional


class BpmnActor(BaseModel):
    id: str
    name: str
    type: str  # human | ai | external_system | output


class BpmnTrigger(BaseModel):
    type: str  # scheduled | event | manual | api_call
    description: Optional[str] = None
    actor_id: str


class BpmnStepDataIO(BaseModel):
    """Named data flowing into or out of a step."""
    name: str
    source_system: Optional[str] = None
    destination_system: Optional[str] = None
    format: Optional[str] = None


class BpmnStep(BaseModel):
    id: str
    name: str
    actor_id: str
    task_type: str = "user"  # user | service | business_rule | manual
    description: Optional[str] = None
    data_inputs: List[BpmnStepDataIO] = Field(default_factory=list)
    data_outputs: List[BpmnStepDataIO] = Field(default_factory=list)


class BpmnDecisionBranch(BaseModel):
    condition: str
    leads_to_step_id: str


class BpmnDecisionPoint(BaseModel):
    id: str
    after_step_id: str
    type: str = "exclusive"  # exclusive | parallel | inclusive
    name: Optional[str] = None
    branches: List[BpmnDecisionBranch] = []


class BpmnExceptionPath(BaseModel):
    id: str
    trigger: str
    from_step_id: str
    leads_to_step_id: Optional[str] = None


class BpmnOutput(BaseModel):
    id: str
    name: str
    after_step_id: str


class BpmnDataObject(BaseModel):
    """A named data object that flows between tasks (Camunda BPMN 2.0).

    Renders in bpmn.io as a small document-icon diamond linked by dotted
    arrows to the tasks that produce / consume it.
    """
    id: str
    name: str
    source_step_id: Optional[str] = None  # step that produces the data
    target_step_id: Optional[str] = None  # step that consumes the data
    format: Optional[str] = None


class BpmnCallActivity(BaseModel):
    """A reusable subroutine invoked from this workflow.

    Rendered as bpmn:callActivity (thicker visual border per Camunda
    convention). Used when an AI step appears across 2+ workflows so it's
    modeled once and called many times.
    """
    id: str
    name: str
    called_element_ref: str  # process id of the called element


class WorkflowSchema(BaseModel):
    workflow_id: str
    workflow_name: str
    description: Optional[str] = None
    actors: List[BpmnActor]
    trigger: BpmnTrigger
    steps: List[BpmnStep]
    decision_points: List[BpmnDecisionPoint] = []
    exception_paths: List[BpmnExceptionPath] = []
    outputs: List[BpmnOutput] = []
    data_objects: List[BpmnDataObject] = Field(default_factory=list)
    call_activities: List[BpmnCallActivity] = Field(default_factory=list)


def validate_workflow_json(data: dict) -> list[str]:
    """Validate a workflow dict against the schema and cross-reference rules.
    Returns list of error strings. Empty list = valid."""
    errors = []
    try:
        wf = WorkflowSchema(**data)
    except Exception as e:
        return [str(e)]

    actor_ids = {a.id for a in wf.actors}
    step_ids = {s.id for s in wf.steps}

    # Validate actor references
    if wf.trigger.actor_id not in actor_ids:
        errors.append(f"Trigger actor_id '{wf.trigger.actor_id}' not in actors")
    for s in wf.steps:
        if s.actor_id not in actor_ids:
            errors.append(f"Step '{s.id}' actor_id '{s.actor_id}' not in actors")

    # Validate decision point references
    for dp in wf.decision_points:
        if dp.after_step_id not in step_ids:
            errors.append(f"Decision '{dp.id}' after_step_id '{dp.after_step_id}' not in steps")
        for br in dp.branches:
            if br.leads_to_step_id != "end" and br.leads_to_step_id not in step_ids:
                errors.append(f"Decision '{dp.id}' branch leads_to '{br.leads_to_step_id}' not in steps")

    # Validate exception paths
    for ep in wf.exception_paths:
        if ep.from_step_id not in step_ids:
            errors.append(f"Exception '{ep.id}' from_step_id '{ep.from_step_id}' not in steps")
        if ep.leads_to_step_id and ep.leads_to_step_id != "end" and ep.leads_to_step_id not in step_ids:
            errors.append(f"Exception '{ep.id}' leads_to_step_id '{ep.leads_to_step_id}' not in steps")

    # Must have at least one step and one actor
    if not wf.steps:
        errors.append("Must have at least one step")
    if not wf.actors:
        errors.append("Must have at least one actor")

    return errors
