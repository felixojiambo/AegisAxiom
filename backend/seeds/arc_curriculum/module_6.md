# Module 6: ROI Scoring with the MetaMetrics Index

## Why MMI exists

By the time you reach Call 3, you have an AI Employee Matrix full of candidate workflows. Every single one of them looks promising — that's why they made it past Call 2. But they're not all equally valuable, and they're definitely not all equally easy to build. Your client doesn't have unlimited budget or unlimited patience. So how do you decide which workflows to lead with in the SOW, which to defer, and which to drop?

The MetaMetrics Index — MMI — is the structured answer. It gives you a single number per workflow that captures both the strategic value and the implementation difficulty, weighted across four dimensions of business impact. You use the score to rank the matrix, and you lead the SOW with the highest-scoring workflows.

This module is the most quantitative one in the certification. There's a formula. There's arithmetic. Don't be intimidated — the math is simple, and the structure is what matters more than the precision of any single calculation.

## The formula

Here's the formula you need to memorize:

**MMI Score = (TSS / IE) × (Time + Cost + Quality + Satisfaction)**

Each of the six inputs is rated on a 1-5 scale. Let's walk through what each one means and how to assess it.

**TSS — Task Strategic Significance.** How much does this workflow matter to the client's business? A workflow that touches revenue generation or customer retention scores high. A workflow that makes back-office reporting slightly less tedious scores low. Think of TSS as the answer to "if this workflow disappeared tomorrow, how much would the business hurt?"

**IE — Implementation Effort.** How hard is this workflow to actually build? Lower IE means easier to build. A workflow with one data source, clean APIs, and a small decision tree scores 1. A workflow that requires custom integration with a legacy mainframe and has fifteen edge cases scores 5. IE is in the denominator on purpose — easier workflows get a higher multiplier.

**Time.** Velocity gain. How much time does the human spend on this today, multiplied by frequency? A daily 4-hour task scores high. A monthly 10-minute task scores low. The signature of a high Time score is "the same person doing the same thing for a meaningful chunk of every day."

**Cost.** Direct labor cost savings. What's the fully loaded cost of the humans currently doing this work? A workflow consuming three contractors at $90/hour each scores high. A workflow consuming 30 minutes of a salaried analyst's day scores low.

**Quality.** Error reduction. How often does the current manual process produce errors, and what's the downstream cost of those errors? A workflow with a 12% error rate that creates expensive rework scores high. A workflow that's already nearly perfect scores low.

**Satisfaction.** This is the dimension reps most often miss. Satisfaction is double-sided: it captures BOTH external customer satisfaction (NPS, CSAT, churn risk) AND internal employee experience (turnover risk on roles stuck doing soul-crushing manual work). A workflow that frees a senior analyst from copy-pasting all day scores high on Satisfaction even if no external customer ever notices the change. Don't make the rookie mistake of treating Satisfaction as "just NPS."

## A worked example

Let's score a real workflow. Say a finance team manually reconciles invoices against payments daily, ~6 hours/day, currently done by two full-time employees making $32/hour fully loaded. Errors are caught downstream about 12% of the time.

- **TSS = 4.** Important back-office function but not a strategic differentiator.
- **IE = 2.** Multiple data sources and edge cases, but not heroic complexity.
- **Time = 4.** 6 hours/day across two FTEs is high.
- **Cost = 4.** Two FTEs at $32/hour fully loaded is real money.
- **Quality = 4.** 12% error rate creates downstream rework, eliminating it has clear value.
- **Satisfaction = 3.** Internal employees hate this work; no external customer touchpoint.

MMI = (4 / 2) × (4 + 4 + 4 + 3) = 2 × 15 = **30**.

A score of 30 puts this workflow solidly in the "build this soon" zone. Nothing exotic, but a clear ROI case that justifies inclusion in the SOW.

## The insight that changes how you sell

Here's the central insight of MMI, and the one you need to internalize: **the score is what ranks the matrix, not any single input.** A high TSS doesn't override a lower total score. The arithmetic decides.

Compare two workflows. Workflow A: TSS=5, IE=2, T=3, C=3, Q=3, S=3 → score = (5/2) × 12 = 30. Workflow B: TSS=4, IE=1, T=3, C=2, Q=3, S=2 → score = (4/1) × 10 = 40.

Workflow A has the higher strategic significance in isolation (TSS=5 vs 4). But its higher implementation effort cuts the multiplier in half. Workflow B's lower IE more than compensates, and Workflow B is the better lead. A "quick win" workflow can absolutely outrank a "big strategic project" for ROI purposes — and clients respect reps who lead with the math instead of with their gut.

This is why MMI exists. Without it, reps lead with whatever seems most impressive, which is often the wrong call. With it, you can defend any prioritization decision with arithmetic.

## How to communicate MMI to skeptics

Some clients will push back. "I don't really care about your scoring methodology — just tell me if this is going to save us money." When you hear this, don't get defensive about the framework. Don't lecture. Reframe.

The reframe is: MMI is not in opposition to the cost question — it's the rigorous way to GET to the cost answer. Every dimension you score is a different category of savings: Time is velocity savings, Cost is direct labor savings, Quality is error-cost savings, Satisfaction is turnover and CSAT savings. When you walk the client through the four dimensions, you're not selling them a methodology — you're showing them the four pockets of money they're currently leaving on the table. Most clients haven't thought about Satisfaction-driven turnover costs at all. The dimensions are a discovery tool as much as they're a scoring tool.

By the time you finish the four-dimension walkthrough, the client usually answers their own question. That's the goal.

## What this enables

Once you can score workflows confidently, you can make the AI Employee Matrix into a real prioritization tool. Sort by score descending, lead the SOW with the highest-MMI workflows, defer the lowest-MMI ones, and have a clear story for why each decision was made. The cut-line is judgment — sometimes it's the top three, sometimes it's the top eight, depending on what scores well and what the client can absorb. Orion will use this ordering when it drafts the SOW. The client will see a methodology, not a guess. And you'll close more deals because the conversation moves from "convince me" to "let's figure out what to build first."
