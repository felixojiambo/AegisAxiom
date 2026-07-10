# BPMN Element Reference for Vega

When constructing workflow JSON for the `generate_bpmn_diagram` tool, use these BPMN element type mappings to ensure the resulting diagram uses correct BPMN 2.0 semantics.

## Task Types

| Workflow Concept           | BPMN Element              | When to Use                                                  |
|----------------------------|---------------------------|--------------------------------------------------------------|
| Human task                 | `bpmn:userTask`           | A person performs the step (e.g., "manager reviews invoice")  |
| Service / system call      | `bpmn:serviceTask`        | Software executes automatically (e.g., "API pulls data")      |
| AI decision                | `bpmn:businessRuleTask`   | AI or rules engine evaluates and decides (e.g., "classify lead") |
| Manual physical action     | `bpmn:manualTask`         | Physical work with no system interaction (e.g., "deliver package") |

## Start Events

| Workflow Concept           | BPMN Element              | When to Use                                                  |
|----------------------------|---------------------------|--------------------------------------------------------------|
| Scheduled trigger          | `bpmn:timerStartEvent`    | Process kicks off on a schedule (e.g., "every Monday at 9 AM") |
| Event trigger              | `bpmn:messageStartEvent`  | Process starts when an external message/event arrives         |
| Manual trigger             | `bpmn:startEvent`         | Process starts when a person initiates it                     |

## Gateways

| Workflow Concept           | BPMN Element              | When to Use                                                  |
|----------------------------|---------------------------|--------------------------------------------------------------|
| Exclusive decision         | `bpmn:exclusiveGateway`   | Exactly one path is taken based on a condition (XOR)          |
| Parallel split             | `bpmn:parallelGateway`    | All outgoing paths execute simultaneously (AND)               |

## Events

| Workflow Concept           | BPMN Element              | When to Use                                                  |
|----------------------------|---------------------------|--------------------------------------------------------------|
| Exception                  | `bpmn:boundaryEvent`      | An error or timeout interrupts a running task                 |
| Output / end               | `bpmn:endEvent`           | The process terminates                                        |

## Data Objects

| Workflow Concept           | BPMN Element                  | When to Use                                              |
|----------------------------|-------------------------------|----------------------------------------------------------|
| Named data passed between tasks | `bpmn:dataObjectReference` + `bpmn:dataObject` | An identifiable artifact moves between steps (e.g., "Approved Invoice", "Customer Email", "Reconciled Report") |
| Task consumes data         | `bpmn:dataInputAssociation`   | Wire a data object reference into a task as an input     |
| Task produces data         | `bpmn:dataOutputAssociation`  | Wire a data object reference out of a task as an output  |

Data objects render as small document-icon diamonds linked by dotted arrows to the tasks that produce or consume them. Use them to make data flow visible at a glance.

## Reusable Subroutines

| Workflow Concept           | BPMN Element              | When to Use                                                  |
|----------------------------|---------------------------|--------------------------------------------------------------|
| Reusable AI step shared across workflows | `bpmn:callActivity` | The same AI task (e.g., "Classify document") is invoked from 2+ workflows. Render once as a call activity and reference it from each caller. Renders with a thicker visual border per Camunda convention. |

## Camunda Naming Conventions

These naming rules are MANDATORY for any element you generate. They keep diagrams readable and consistent with how Camunda's reference docs expect process elements to read:

- **Tasks** — object + verb infinitive. **YES:** "Review invoice", "Approve refund", "Reconcile statement". **NO:** "Process invoice" (vague verb), "Invoice processing" (gerund), "Handle the thing".
- **Events** — object + past-tense state describing what *happened*. **YES:** "Invoice paid", "Order shipped", "Refund approved". **NO:** "Pay invoice" (that's a task), "Payment" (no verb).
- **Gateways** — phrased as a question with a `?`. **YES:** "Amount > $10K?", "Customer has SLA?", "Document type?". **NO:** "Approval check" (not a question).
- **Lanes** — role or system name in title case. **YES:** "Sales Ops", "NetSuite", "Account Manager". **NO:** "Worker 1", "user".
- **Data Objects** — noun-case naming the artifact. **YES:** "Approved Invoice", "Customer Email", "Reconciled Report". **NO:** "data1", "input", "the data".
- **Avoid generic verbs** in task names: "Handle", "Process", "Manage", "Do" — replace with specific verbs like "Validate", "Approve", "Reconcile", "Classify", "Notify".

### Why naming matters
Diagrams get read by people who weren't on the discovery call — engineers, project managers, the client themselves during the Call 3 review. Object+verb task names and past-tense event names let a reader skim a diagram and immediately understand what happens at each step without translating jargon. Generic names ("Process invoice") force the reader to ask "process *how*?", and the only way to answer is to dig back into the discussion. Specific names ("Validate PO number against NetSuite") carry their own context, which is what makes the BPMN actually useful as a deliverable.
