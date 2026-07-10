# Vega Knowledge Base — Power Questions Reference

**Source:** Bree's AEGISAXIOM Ecosystem deep research doc, Phase 2 — Strategic Questioning section.
**Purpose:** This file is loaded into Vega's knowledge base and serves two functions: (1) when a sales rep asks Vega to draft a Call 1 agenda before any transcript exists, Vega pulls from this; (2) when Vega analyzes a transcript, it uses these questions as the checklist to detect what the rep failed to ask, generating the "missed questions" list for the next call.

---

## The AI Recruiter philosophy — read this before applying any question

Sales reps using Vega are not traditional software vendors. They are positioned as **AI Recruiters** — strategic consultants who help organizations recruit, interview, and deploy digital labor. This framing fundamentally changes how questions are asked and how answers are interpreted.

A traditional software seller asks "what features do you need?" An AI Recruiter asks "what work is bottlenecked, and which of that work should be done by a digital employee instead of a human?"

When Vega detects that a rep is asking traditional software-vendor questions in a discovery call (e.g., "what's your budget for software?", "are you evaluating other vendors?", "do you need single sign-on?"), Vega should flag this in the "missed questions" output and recommend reframing toward workflow and labor questions in the next call. The AI Recruiter positioning is the foundation; the power questions below are the tactical execution of that positioning.

The certification value: a certified AI Recruiter engages with clients in conversations about organizational design, workforce scaling, and human capital optimization — driving deeper trust, C-suite engagement, and larger deal sizes than a traditional software pitch ever could. Vega's job is to make sure every rep operates at this level of sophistication.

---

## Call 1: General State and Pain Identification

**Goal of Call 1:** Diagnostic. Uncover systemic friction, map the high-level data landscape, establish trust without diving into premature technical solutioning. Typical duration: 30-60 minutes.

**What Vega should listen for:** broad pain themes, data state signals (Blind / Siloed / Visible), stakeholder dynamics, mentions of expensive employees stuck doing low-value work. Vega should NOT expect specific workflow logic, exact technical parameters, or scoping decisions — those are Call 2 territory. If a rep tries to extract Call 2 detail in Call 1, Vega should flag this as a pacing problem.

### The five Call 1 power questions

#### 1. Situational Awareness
> "Walk me through the lifecycle of [the process], from the moment the initial trigger occurs to the final point of resolution. What are the major waypoints?"

**What Vega should listen for in the answer:**
- Sequential structure — does the client describe the process in clear phases, or jump around?
- Named systems — what tools come up by name?
- Implicit handoffs between people or teams.
- Vague descriptions ("we just kind of figure it out") that signal an undocumented or improvised workflow.

**Flag in missed questions if:** the rep moved on without getting at least 3-4 named waypoints in the lifecycle. A vague answer is worse than no answer; it means the workflow isn't well-understood by the client either.

---

#### 2. Friction Identification
> "In looking at that lifecycle, where do your teams experience the most frequent delays, require the most manual intervention, or encounter the highest error rates?"

**What Vega should listen for:**
- Specific time costs ("we spend 4 hours a day on this") — these are MMI inputs.
- Frustration language ("it drives me crazy that..." "we're constantly chasing...").
- Specific error rates or rework percentages.
- Mentions of approval bottlenecks or waiting periods.

**Flag in missed questions if:** the rep got friction descriptions but no quantification. Without time costs or error rates, the MMI hypothesis for these workflows will be low-confidence.

---

#### 3. Cost of Inaction
> "What is the compounding business impact — in terms of lost revenue, wasted labor hours, or compliance risk — if these specific bottlenecks are not resolved within the next two quarters?"

**What Vega should listen for:**
- Dollar figures, even rough ones.
- Compliance / regulatory framing ("we'd fail an audit," "we're at risk for HIPAA fines").
- Customer-facing consequences (churn, NPS hits, missed SLAs).
- Strategic consequences (can't launch a new product, can't expand to a new region).

**Flag in missed questions if:** the rep got pain identification but skipped the financial impact framing. This is the difference between "we have a problem" and "we have a $1.2M/year problem." The technical team needs the latter to build accurate pricing.

---

#### 4. Data Landscape Probing
> "Where does the information required to complete these tasks currently live? Are your teams working out of a centralized system, or are they constantly switching between disconnected applications to find what they need?"

**What Vega should listen for:**
- This is the primary signal for the data state classification (Blind / Siloed / Visible). See the "Data State Diagnostic" section of Vega's curriculum modules for the full taxonomy.
- Number of distinct systems mentioned (4+ usually indicates Siloed).
- Mentions of physical paperwork, whiteboards, sticky notes, or "tribal knowledge" living in employees' heads (indicates Blind).
- Mentions of unified data warehouses, REST APIs, governed access (indicates Visible).

**Flag in missed questions if:** the rep classified data state without supporting evidence from the client's own words. Vega's `data_state_justification` field requires specific quotes or paraphrases — if the rep didn't extract them, Vega can't write them.

---

#### 5. Strategic Vision
> "When you look at your highest-paid, most talented employees, what administrative or repetitive tasks are preventing them from doing the strategic work you actually hired them to do?"

**What Vega should listen for:**
- Named senior employees and their wasted time (a goldmine for the labor arbitrage MMI dimension).
- Hourly rate signals — even indirectly. "Our VP of Sales spends 10 hours a week on this" is much more financially significant than "an analyst spends 10 hours a week."
- Mention of strategic projects that aren't getting done because of administrative drag.

**Flag in missed questions if:** the rep didn't surface at least one named senior stakeholder and one specific strategic project they're being prevented from doing. Without this, the "Cost" MMI dimension will undershoot the real value.

---

## Call 2: Deep Dive and Workflow Scoping

**Goal of Call 2:** Granular, surgical scoping. The rep follows Vega's Call 1 agenda to map the exact technical and operational parameters of the workflows identified in Call 1. Typical duration: 60-90 minutes.

**What Vega should listen for:** specific technical parameters per workflow — triggers, data sources, decision rules, exception paths, completion criteria. Vega should expect the rep to be working through the agenda systematically, workflow by workflow. If the conversation drifts back into general pain identification, Vega should flag this — Call 2 should not be re-running Call 1.

**Critical:** every workflow described in Call 2 should have ALL FIVE of the categories below covered. If a workflow has trigger and dependency mapping but no exception handling, that workflow is incomplete and cannot be diagrammed by the BPMN tool. Vega should refuse to generate a BPMN diagram for an incomplete workflow and add the missing category to the Call 3 agenda.

### The five Call 2 power questions

#### 1. Trigger Mechanisms
> "What specific event, inbound data point, or communication initiates this workflow? Is it an email, a form submission, or a system alert?"

**Maps to BPMN element:** Start Event (with type — message, timer, signal, etc.).

**What Vega should listen for:**
- Specific trigger type — scheduled / event-driven / manual / API call.
- Source system — where does the trigger come from?
- Frequency — how often does it fire?
- Initiator — which actor (human or system) starts it?

**Required for BPMN generation.** If the trigger isn't fully specified, Vega cannot generate the diagram.

---

#### 2. Dependency Mapping
> "Before a human can make an approval decision on this step, what exact information must they retrieve, and from which specific databases or documents?"

**Maps to BPMN element:** Sequence flows + data inputs + service tasks for system lookups.

**What Vega should listen for:**
- Named systems and the specific data fields pulled from each.
- Manual lookup steps that could be automated.
- Cross-system data correlation (e.g., "we have to match the invoice number from QuickBooks against the PO number in Excel").

**Flag for BPMN generation:** dependency mapping must specify which system holds which data. "We pull customer info" is too vague; "we pull customer email and account ID from Salesforce" is sufficient.

---

#### 3. Logic Rules
> "What are the exact, documented rules your team follows to determine if this request should be approved, rejected, or escalated?"

**Maps to BPMN element:** Exclusive Gateway (decision point) with branch conditions.

**What Vega should listen for:**
- Specific thresholds with numeric values ("anything over $5k goes to the VP").
- Multi-condition rules ("if it's over $5k AND it's a new vendor, both conditions").
- Implicit rules ("we usually..." — these are NOT documented rules, flag as undocumented).
- Mention of rules that vary by stakeholder ("Sarah always does it this way, but Mike does it differently") — this is a red flag for inconsistent process.

**Required for BPMN generation.** Decision points without specified branch conditions cannot be diagrammed. If the client says "we just kind of know," Vega should flag this as an undocumented logic rule and add a Call 3 question to extract the actual decision criteria.

---

#### 4. Exception Handling
> "When this standard process fails or encounters an anomaly (an edge case), what are the exact steps taken to resolve it, and who is responsible for that resolution?"

**Maps to BPMN element:** Boundary Events on the affected tasks, plus exception flows leading to compensation or escalation.

**What Vega should listen for:**
- Named exception scenarios ("if the invoice doesn't have a PO," "if the customer's payment fails").
- Escalation paths and the people involved.
- Manual workarounds that have become routine — these often indicate the "real" process is different from the documented one.
- Mention of exceptions that "almost never happen" — probe these, because they often turn out to happen weekly.

**Required for BPMN generation only if exceptions exist.** If a workflow has no exception handling, that's not necessarily a gap — but Vega should explicitly confirm with the rep "did the client say there are no exceptions, or did the rep not ask?"

---

#### 5. Outcome Definition
> "What defines the absolute completion of this workflow? What systems must be updated, what notifications must be sent, and what reports are generated to reflect that completion?"

**Maps to BPMN element:** End Event(s), with outputs.

**What Vega should listen for:**
- Specific systems updated at completion (e.g., "the CRM stage moves to 'Closed Won' and the invoice gets generated in QuickBooks").
- Notifications sent and to whom.
- Reports generated and where they live.
- Any post-completion processes that should be considered part of this workflow vs. a separate one.

**Required for BPMN generation.** A workflow without a defined endpoint cannot be diagrammed — every BPMN process needs at least one End Event.

---

## Call 3: Resolution and Confirmation (Dynamic Questions)

**Goal of Call 3:** Final review. Confirm gaps from Call 2 are resolved, address Vega-flagged contradictions, run MMI scoring with the client, lock the scope and finalize the discovery package for pricing.

**Call 3 questions are primarily dynamic**, generated from Call 2 gaps and contradictions. However, the static confirmation probes in the "Call 3: Final Review and Confirmation" section below provide a reusable framework for structuring the conversation. Vega generates the dynamic Call 3 agenda from:

1. **Gaps from Call 2** — workflows where one or more of the five Call 2 categories were incomplete.
2. **Contradictions across calls** — places where Call 1 and Call 2 produced incompatible information (e.g., Call 1 said all data was in Salesforce, Call 2 mentioned Excel exports).
3. **Low-confidence MMI dimensions** — workflows where Vega had to mark `needs_client_input: true` on Cost or Satisfaction or any other dimension because there wasn't enough information from the calls.
4. **Stakeholder confirmation** — if Call 1 or Call 2 mentioned named stakeholders who weren't on the call, Call 3 may need their direct input on workflows they own.

**What Call 3 questions look like:** rather than the open-ended diagnostic questions of Call 1 or the systematic technical questions of Call 2, Call 3 questions are surgical and specific. Examples Vega might generate:

- "In Call 1 you mentioned all customer data lives in Salesforce, but in Call 2 you mentioned the sales team exports to Excel for forecasting. Which is the source of truth for forecasting purposes?"
- "We have your invoice cross-referencing workflow scoped except for the approval threshold. What dollar amount triggers escalation from the AP clerk to the Finance Manager?"
- "For the procurement workflow, how would you score the 'Cost' dimension on a 1-5 scale? We weren't able to extract enough from the prior calls to estimate this confidently."
- "You mentioned Sarah Chen as the Finance Manager but she wasn't on either of our calls. Is there a separate conversation we need to have with her before we can finalize the procurement workflow scope?"

**Vega's job for Call 3 prep:** when generating the Call 3 agenda, group dynamic questions by workflow, then within each workflow group, by category (gaps, contradictions, MMI, stakeholders). The rep should be able to walk through the agenda workflow-by-workflow during the call. Vega should also estimate how long the call will take based on the number of items (rule of thumb: 3-5 minutes per dynamic question).

---

## Anti-questions: things the rep should NOT be asking in any of the three calls

When Vega detects these patterns in a transcript, it should flag them in the "rep training notes" section of the artifact (a new free-form field for the rep's own learning). These are signs that the rep is reverting to traditional software-vendor positioning and breaking the AI Recruiter framing.

- **"What's your budget for AI?"** — Wrong framing. AI Recruiter asks about labor costs and lost productivity, not software budgets. The right reframe: "What's the fully-loaded annual cost of the team currently doing this manual work?"
- **"Are you evaluating other vendors?"** — Wrong framing. There are no competing AI vendors in this conversation; the question is "are you continuing to use humans for this work, or are you ready to deploy a digital employee?" Reframe: "What are the alternatives you've considered for solving this bottleneck?"
- **"Do you need single sign-on / SAML / SOC 2 compliance?"** — Premature. These are deployment-time questions, not discovery-time questions. Wait until after the workflow is scoped and prioritized.
- **"What features are most important to you?"** — Backwards. AI is not a feature checklist. Reframe: "What outcomes would change for your business if this workflow were fully automated?"
- **"Can I send you a demo?"** — Premature solutioning. Discovery is about diagnosing the problem; demos come after the prescription is written. Reframe: "Can I send you a draft AI Employee profile based on what you've described, so you can react to it before our next call?"

Vega flags these patterns in the analysis output not to shame the rep, but because the certification program expects continuous improvement. Spotting these in transcripts is how reps level up.

---

---

## Call 2: Workflow Deep-Dive and Scoping — Conversational Probes

**Goal of Call 2:** Precision mapping. For each workflow identified in Call 1, extract the full process flow: triggers, steps, decision points, exception paths, data inputs/outputs, and integration points. Duration: 45-90 minutes depending on complexity.

**What Vega should listen for:** Specific process sequences, named decision thresholds, error handling procedures, system-to-system data flows, time/cost metrics for MMI scoring. Call 2 is where workflows graduate from "we have a problem" to "here's exactly how the process works and where AI fits."

### The five Call 2 power questions (per workflow)

#### 1. Trigger Mapping
> "Walk me through exactly what happens to kick off [workflow]. Is it a scheduled task, an incoming event, or does someone manually start it? How do you know it's time to start?"

**What Vega should listen for:**
- Trigger type: scheduled (bpmn:timerStartEvent), event-driven (bpmn:messageStartEvent), or manual (bpmn:startEvent)
- Trigger frequency and volume
- Whether the trigger is reliable or sometimes missed

**Flag in missed questions if:** the trigger is described vaguely ("it just kind of happens when we need it") — this is a scoping blocker.

---

#### 2. Step-by-Step Process Walk
> "Now take me through each step, one at a time. Who does what, what system are they in, and what data do they need to complete that step?"

**What Vega should listen for:**
- Sequential steps with named actors (bpmn:userTask for humans, bpmn:serviceTask for systems)
- Data inputs per step (what system, what format)
- Data outputs per step (what's produced, where it goes)
- Handoff points between people or systems

**Flag in missed questions if:** fewer than 5 steps were extracted for a non-trivial workflow. Ask for intermediate steps that must logically exist.

---

#### 3. Decision Point Deep-Dive
> "At [decision point], what determines which path you take? Is it a hard threshold, a judgment call, or does it depend on who's doing it?"

**What Vega should listen for:**
- Decision type: rule-based (bpmn:businessRuleTask candidate) vs. judgment (stays as bpmn:userTask)
- Specific thresholds or criteria ("over $5K needs VP approval")
- Variance: does the same decision always go the same way, or does it depend?
- This directly informs whether a bpmn:exclusiveGateway can be automated

**Flag in missed questions if:** decision criteria are described as "it depends" without specifics. Push for concrete conditions.

---

#### 4. Exception Path Discovery
> "What happens when something goes wrong in this workflow? What's the most common failure, and what does the recovery process look like?"

**What Vega should listen for:**
- Named exception types (bpmn:boundaryEvent candidates)
- Recovery procedures (manual intervention, escalation, retry)
- Frequency of exceptions ("it happens maybe twice a month")
- Cost of exceptions ("when it fails, it takes 2 hours to fix")

**Flag in missed questions if:** the client says "it doesn't really fail" — every process has exception paths. Probe harder.

---

#### 5. Volume and Cost Quantification
> "How many times does this workflow run per [week/month]? And roughly how long does each instance take from start to finish? What would you estimate the fully-loaded cost per instance is?"

**What Vega should listen for:**
- Volume metrics (instances per time period)
- Duration metrics (time per instance)
- Cost metrics (labor cost, opportunity cost)
- These are CRITICAL inputs for MMI scoring — without them, all dimensions are low-confidence

**Flag in missed questions if:** the rep got qualitative answers ("a lot", "too long") without quantification. Push for numbers.

---

## Call 3: Final Review and Confirmation — Static Power Questions

**Goal of Call 3:** Confirmation and gap resolution. Walk the client through the AI Employee Matrix, confirm workflow accuracy, resolve contradictions, finalize MMI scores, and lock the scope for SOW generation. Duration: 30-45 minutes.

**What Vega should listen for:** Corrections to workflow descriptions, updated metrics, resolved contradictions, stakeholder alignment signals, scope changes. Call 3 should generate very few NEW workflows — it should mostly confirm and refine.

### The five Call 3 power questions (per workflow)

#### 1. Matrix Walk-Through Confirmation
> "I want to walk you through what we've mapped. For [workflow]: we have it starting with [trigger], going through [steps], with [decision points]. Does that match how it actually works?"

**What Vega should listen for:** Corrections, missing steps, incorrect assumptions. Any "well, actually..." moments are gold — they reveal what the prior calls missed.

**Flag in missed questions if:** the rep presented workflows without asking for explicit confirmation. Silence is not agreement — the client may have been too polite to correct.

---

#### 2. Score Validation
> "We've estimated the time savings at [X] and the integration effort at [Y]. Does that feel right to you, or would you adjust those?"

**What Vega should listen for:** Client's gut-check on MMI dimensions. If they say "actually it's much harder than that" for integration effort, adjust IE upward. This is where low-confidence dimensions become high-confidence.

**Flag in missed questions if:** the rep presented scores without inviting challenge. MMI scores need client validation to move from "medium" to "high" confidence.

---

#### 3. Priority Confirmation
> "If you could only automate one of these workflows this quarter, which would have the biggest impact on your team?"

**What Vega should listen for:** Business priority signals. This may differ from MMI-ranked priority — and that's important context for SOW phasing.

**Flag in missed questions if:** the rep assumed MMI ranking equals client priority. Business context (political, strategic, seasonal) can override the numbers.

---

#### 4. Stakeholder Alignment
> "Who else needs to sign off on this? Is there anyone who might push back on automating [workflow]?"

**What Vega should listen for:** Blockers, champions, budget holders. This feeds the stakeholder strategy section of the design document.

**Flag in missed questions if:** the rep closed the call without identifying the decision-maker and the approval process. Without this, the SOW may be sent to the wrong person.

---

#### 5. Data Access Confirmation
> "For the systems we've identified — [system list] — do you have API access, or would we need to set that up? Who controls those credentials?"

**What Vega should listen for:** Technical access reality. Many workflows look automatable until you discover the system has no API and the vendor charges $50K for one. This directly impacts IE (Integration Effort) scoring.

**Flag in missed questions if:** the rep did not confirm API availability for every named system in the workflow. Assumed access is the number one cause of scoping surprises.

---

**End of power questions reference.**
