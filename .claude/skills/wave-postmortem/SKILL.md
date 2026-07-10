---
name: wave-postmortem
description: Activates after every wave completes. Captures agent outcomes,
  verifies file ownership, runs smoke tests, classifies carry-forward work,
  tracks duration, writes postmortem and PM companion report, updates master
  log, and hands off to wave-router. Never skip this step.
---

# Wave Postmortem

Run immediately after every wave, before any next-wave planning. Never skip it.

## Step 1 — Collect agent outcomes
For each agent: status, what was delivered, blockers, discoveries, files modified, duration.

## Step 2 — Verify file ownership
Compare each agent's files modified against their assigned scope. Violations → MUST FIX.

## Step 3 — Reconciliation (parallel design waves only)
If 2+ agents produced design specs: read all, identify conflicts, produce reconciled version.

## Step 4 — Smoke testing
Verify outputs work: imports resolve, no syntax errors, routes return 200, configs load.
Failures agents didn't self-report → MUST FIX.

## Step 5 — Classify carries forward
- **MUST FIX** — blocks other work or broke something
- **SHOULD ADD** — discovered improvement, evaluate against priorities
- **WATCH** — noted but not yet actionable

## Step 6 — Check recurring items
If any MUST FIX appeared in 2+ consecutive postmortems: escalate to human, don't auto-roll.

## Step 7 — Write postmortem
Save to `.waves/wave-{N}-postmortem.md` with: Agent Outcomes table (with model and duration),
File Ownership Compliance, Reconciliation (if applicable), Smoke Test Results,
MUST FIX / SHOULD ADD / WATCH / Recurring Items, Context for Next Wave.

## Step 8 — Update master log
Append to `.waves/master-log.md`: duration, agent counts, item counts, violation counts.

## Step 9 — Write PM companion report
Save to `.waves/wave-{N}-update.md`: what was delivered, what changed for users,
what's next, decisions needed. Plain language, no agent jargon. Under 1 page.

## Step 10 — Hand off to wave-router
Pass postmortem to wave-router. MUST FIX items appear in next wave before any new work.
