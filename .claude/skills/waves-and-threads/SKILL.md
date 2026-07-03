---
name: waves-and-threads
description: Use when dispatching multiple agents in parallel where tasks
  have dependency ordering. Coordinates wave execution, thread isolation,
  mandatory code review, and handoff to wave-postmortem on completion.
---

# Waves and Threads

## Definitions

A **wave** is a group of tasks dispatched simultaneously to parallel agents.
Wave N+1 does not start until Wave N completes AND the postmortem has been written.

A **thread** is an independent chain of tasks in isolation. Threads do not share
state or files unless explicitly listed in the wave plan.

## Protocol

### Step 1 — Dependency analysis
Map every task against its dependencies. If a task requires output from another
incomplete task, it cannot be in the same wave.

### Step 2 — Wave assignment
- Wave 1: tasks with no dependencies
- Wave 2: tasks depending only on Wave 1 output
- Never put interdependent tasks in the same wave

### Step 3 — Agent assignment
Consult wave-router for agent personas, models, shared constraints, and contracts.

### Step 4 — Confirm before dispatch
Wait for explicit human confirmation before dispatching Wave 1.

### Step 5 — Dispatch
Launch all agents in the wave simultaneously. Track completion per agent.

### Step 6 — Mandatory code review
After any wave where 2+ agents produced code in parallel:
1. Integration Agent fixes cross-boundary mismatches
2. Senior Code Reviewer checks all code for bugs, security, correctness
Critical/high findings must be fixed before the next wave.

### Step 7 — Trigger postmortem
Immediately activate wave-postmortem. Do not begin next-wave planning until
the postmortem file exists at `.waves/wave-{N}-postmortem.md`.

## Thread Isolation Rules
- Each file owned by exactly one agent per wave — no overlap
- Agents must only modify files in their assigned scope
- If an agent needs a file outside its scope, it notes a blocker — not modify it
- Merge order: lowest-risk first, integration tests between each merge

## Wave Failure Handling
- Don't cancel the wave — let other agents complete
- Record failure as MUST FIX in postmortem
- Re-run only the failed task in the next wave

## Rollback Protocol
If a wave is fundamentally broken (not just fixable bugs):
1. Record in postmortem with explanation
2. Surface to human with options: re-run, revert, or proceed with partial output
3. Wait for human decision
