---
name: wave-router
description: Use before any parallel agent dispatch. Reads the previous
  wave postmortem (if one exists), defines shared constraints, selects
  agent personas with model assignments, defines interface contracts,
  and outputs a wave plan for human confirmation. Always runs before
  waves-and-threads dispatches agents.
---

# Wave Router

## Role

The wave-router sits between planning and dispatch. It:
1. Reads the previous postmortem to determine what carries forward
2. Defines shared constraints that all agents must follow
3. Assigns the right agent persona and model to each task
4. Defines interface contracts for cross-agent boundaries
5. Produces a plan and waits for confirmation

It does not execute tasks. It produces a plan and waits for confirmation.

---

## Step 1 — Check for a previous postmortem

Before doing anything else, check for `.waves/wave-{N-1}-postmortem.md`.

**If a postmortem exists:**
- Read it in full before touching the task list
- All MUST FIX items become the first tasks in the next wave — before any new work
- SHOULD ADD items are evaluated against current priorities and included or deferred
- The Context section is passed to every agent in the upcoming wave as additional briefing
- Check the Recurring Items section (see Step 2)

**If no postmortem exists:**
- This is Wave 1 — proceed directly to Step 3

---

## Step 2 — Recurring item check

Before generating any wave plan, scan all available postmortems for recurring items —
tasks or blockers that have appeared in two or more consecutive postmortems.

**If a recurring item is found:**
- Do not roll it forward into the next wave automatically
- Surface it to the human with a plain summary:
  - What the item is
  - How many waves it has appeared in
  - What has been tried so far
- Wait for a human decision before including it in any wave plan
- Do not proceed with wave planning until the human responds

This rule exists because a silently recurring blocker compounds across every wave.
The system should move forward — but not blindly.

---

## Step 3 — Define shared constraints

**Before assigning any agents**, define the non-negotiable shared constraints that
every agent in this wave must follow. This prevents parallel agents from making
conflicting decisions that require expensive reconciliation later.

Shared constraints must cover (where applicable):

- **Naming conventions** — variable naming style, file naming, route patterns
- **Data model shape** — exact field names, types, and structures that will be shared
  across agent boundaries
- **Route structure** — URL patterns, HTTP methods, response formats
- **Technology decisions** — framework versions, libraries, patterns already chosen
- **File structure** — where new files go, what directories are owned by whom

Include these constraints in the wave plan output AND in each agent's prompt.
Agents must not deviate from shared constraints without flagging a conflict.

---

## Step 4 — Agent assignment

For each task in the wave plan, assign exactly one agent persona and a model.

### Agent selection

Use agents available in `~/.claude/agents/` if present. If the user does not have
custom agent files (common in team setups), fall back to the **built-in subagent type**.

| Domain | Custom Agent File | Built-in Fallback (subagent_type) |
|---|---|---|
| API design, Cloud Run, async patterns | Backend Architect | `Backend Architect` |
| Backend implementation | backend-dev | `backend-dev` |
| Database schema, queries, migrations | Database Optimizer | `Database Optimizer` |
| Cross-cutting system design | Software Architect | `Software Architect` |
| Complex implementation | Senior Developer | `Senior Developer` |
| Heavyweight code review before commit | Senior Code Reviewer | `Code Reviewer` |
| Routine lightweight code review | code-reviewer | `code-reviewer` |
| Frontend implementation | frontend-dev | `frontend-dev` |
| Branching, commits, merge conflicts | Git Workflow Master | `Git Workflow Master` |
| Production readiness, SLOs, observability | SRE | `SRE (Site Reliability Engineer)` |
| Production incident or breakage | Incident Response Commander | `Incident Response Commander` |
| Multi-agent coordination | Agents Orchestrator | `Agents Orchestrator` |
| System flow mapping, pre-code planning | Workflow Architect | `Workflow Architect` |
| Rules engine, trigger logic, workflow design | Automation Governance Architect | `Automation Governance Architect` |
| Curriculum, certification, training systems | Corporate Training Designer | `Corporate Training Designer` |
| Stakeholder input → actionable specs | Feedback Synthesizer | `Feedback Synthesizer` |
| Pre-merge production readiness gate | Reality Checker | `Reality Checker` |
| API surface testing, contract validation | API Tester | `API Tester` |
| Infrastructure, CI/CD, Docker, deploys | devops | `devops` |
| QA, testing, test suites | qa-engineer | `qa-engineer` |
| Fullstack feature coordination | team-lead | `team-lead` |
| Cross-boundary integration after parallel waves | **Integration Agent** (see below) | `code-reviewer` |

If no agent is a clear fit, assign Senior Developer as the default.

### Integration Agent role

After any wave where 2+ agents worked in parallel on code that must integrate,
schedule an **Integration Agent** in the next wave whose sole job is:
- Read all outputs from the parallel wave
- Fix cross-boundary mismatches (variable names, imports, data shapes)
- Ensure all agent outputs work together
- Run before the Senior Code Reviewer

This reduces the orchestrator's manual integration burden.

### Model selection

Assign a model to each agent based on the task complexity:

| Task type | Recommended model |
|---|---|
| Architecture, design, complex reasoning | `opus` |
| Implementation, feature work | `sonnet` |
| Code review, final quality gate | `opus` |
| Simple fixes, formatting, config | `sonnet` |

Include the model in the wave plan's agent assignment table.

### Agent briefing

Each agent receives:
- Their scoped task description
- Their persona file from `~/.claude/agents/` (or built-in subagent_type)
- The files they are **permitted** to modify (exclusive — no overlap between agents)
- The shared constraints from Step 3
- Any interface contracts relevant to their work (Step 5)
- Any context carried forward from the previous postmortem
- **The git-operations safety rules from `waves-and-threads/SKILL.md`**
  (mandatory boilerplate for every implementation agent — covers no-merge,
  explicit pathspec commits, no destructive operations)

### Worktree isolation (default for parallel implementation waves)

For waves where 2+ implementation agents will write code, dispatch each
with `isolation: "worktree"` so they operate on independent working
trees. The orchestrator merges branches at the end of the wave under
controlled conditions.

Worktree isolation is the durable fix for the recurring "same-file
parallel writes" coordination problem (see waves-and-threads/SKILL.md
"Worktree isolation" for the failure modes). It also eliminates the
destructive-auto-merge class of incident.

Skip worktree isolation only when:
- The wave is a single implementation agent (no parallelism to isolate)
- The wave is design / planning / review only (no code being written)
- Worktree isolation is unavailable in the environment

When skipping, the git-safety rules in the agent prompt become the
only line of defense — make sure they are present.

---

## Step 5 — Define interface contracts

For any wave where multiple agents will produce code that must work together,
define explicit **interface contracts** before dispatch. An interface contract
specifies the exact boundary between two agents' work.

Each contract must include:

- **Boundary**: which two agents / components this contract connects
- **Function signatures**: exact function names, parameter names and types, return types
- **Data shapes**: exact variable names, dictionary keys, object structures
- **File paths**: where shared code lives, what import paths to use
- **Template variables**: if one agent writes backend and another writes templates,
  list every variable name the template expects

Example:
```
Contract: Backend → Frontend template
  Route: GET /dashboard
  Template: dashboard.html
  Variables passed: { repos: dict[str, list[dict]], stats: { total_repos: int, new_today: int } }
  Each repo dict keys: full_name, url, stars, language, description, ai_summary
```

Include all contracts in the wave plan output AND in each relevant agent's prompt.
Agents must use the exact names from the contract — not infer or guess.

### Producer-side consumer enumeration (introducing a contract)

When a thread INTRODUCES a new interface contract (new prop, new callback,
new response shape, new event payload), its prompt must instruct the agent to
**enumerate every current consumer of the surface being changed BEFORE finalizing**.

This is the producer's mirror of the reviewer rule below. Skipping it means:
- The producer ships a "new prop" without wiring it through the consumers
- Consumers that don't yet pass the new prop silently get `undefined` / a no-op
- The bug ships unless the reviewer wave catches it after the fact

Add this to producer prompts whenever the work introduces a new prop, callback,
or contract:

> "When you introduce a new prop / callback / contract, grep for every callsite
> / mount-point of the surface being changed and verify each consumer either
> passes the new contract OR has a documented graceful fallback. List every
> consumer in your report. This is mandatory — silent no-ops are not acceptable."

Real examples where this rule would have prevented fix-up cycles:
- **v1.2 Wave 2A** — added inline edit pickers to `TaskCard`. Did not enumerate
  every view that mounts `TaskCard`. 5 views missed `onTaskMutated` wiring.
- **v1.3 Wave 2A** — added `onConflict` prop to `TaskDetailDrawer` flow. Did
  not enumerate the 8 views that mount `TaskDetailDrawer`. All 8 dropped the prop.
- **v1.3 Wave 2B** — added `tasks` prop to `BulkActionBar` for undo snapshot.
  Did not enumerate BoardView + BacklogView callsites. Both omitted, silent no-op.

Each was caught by reviewers (good) but generated a fix-up wave (avoidable).

### Reviewing interface-contract changes

When a wave introduces, changes, or removes an interface contract (a new callback
prop, a renamed handler signature, a swapped response shape), the code-review wave
that follows **must enumerate every consumer of the contract**, not just review the
producer's diff.

The default review pattern is "read the diff and look for bugs." That pattern misses
**wiring gaps**: consumers that were not migrated, where the new contract silently
no-ops because the new prop / callback is `undefined`. The producer's diff looks
clean, the consumer's diff doesn't exist, and the bug ships.

For every interface-contract change in a wave, the code review wave must:

1. Grep for every callsite / mount-point of the changed component
2. Confirm each consumer either passes the new contract OR has a working fallback
3. Flag any consumer that was not migrated — even if the code "still compiles"
4. Confirm the producer has a safe fallback for un-migrated consumers (e.g.
   `onTaskMutated?.(data); /* else fall through */`), so a missed wiring becomes
   a degraded UX, not a silent regression

This applies to: React props that change (added/removed/renamed callbacks), API
response shapes that change, function signatures that change, event payloads that
change, anything where producer and consumer were coupled by name or shape.

Real example: in projects-v1.1, the producer rewrote `TaskDetailDrawer.patch()` to
call `onTaskMutated(updatedTask)` instead of `onTaskUpdated()`. Code review approved
the producer's diff. The Reality Checker caught that 5 of 8 consumer views never
received the new prop, leaving the original bug intact in production. The lesson:
**diff review is necessary but not sufficient for contract changes; consumer
enumeration is mandatory.**

---

## Step 6 — Output the wave plan

Output the full plan in this format before dispatching anything:

```
## Wave N Plan

### Shared Constraints
- Naming: [convention]
- Data model: [key shapes]
- Routes: [patterns]
- Tech: [decisions]

### Carried forward from Wave N-1
MUST FIX:
  - [item]
SHOULD ADD (included):
  - [item]
SHOULD ADD (deferred):
  - [item]

### Interface Contracts
[Contract 1: Agent A → Agent B]
  - [exact signatures, variable names, data shapes]

[Contract 2: Agent B → Agent C]
  - [exact signatures, variable names, data shapes]

### Agent Assignments
| Agent | Model | Task | Files (exclusive) |
|-------|-------|------|--------------------|
| [name] | opus/sonnet | [scoped task] | [file scope] |
| [name] | opus/sonnet | [scoped task] | [file scope] |

### Mandatory follow-up
- Code Review wave after this wave completes (required for implementation waves)
- Integration Agent if 2+ agents modified code (before code review)

### Context passed to all agents
[Paste relevant section from previous postmortem, or "None — Wave 1"]
```

---

## Step 7 — Wait for confirmation

After outputting the plan, stop. Do not dispatch anything.

Wait for explicit human confirmation before handing off to waves-and-threads.

If the human requests changes to the plan, revise and re-present.
Only dispatch after receiving a clear go-ahead.

---

## Handoff

Once confirmed, pass the wave plan to the waves-and-threads skill for dispatch.
Include the full agent assignments, shared constraints, interface contracts, and
any context from the postmortem so waves-and-threads does not need to re-read
the postmortem itself.
