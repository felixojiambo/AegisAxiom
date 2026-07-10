# Module 5: The 3-Call Discovery Workflow & RACI

## Why three calls (not one, not five)

You might ask: why three calls? Why not just one big discovery meeting, or a longer sales cycle with five touchpoints? The answer is that three calls maps almost perfectly to the natural rhythm of a complex AI engagement, and any deviation from it creates predictable failure modes.

One call doesn't work because no client can describe their operations completely in a single sitting, and no AI Recruiter can analyze a transcript thoroughly enough to scope an engagement in real time. You'd be guessing.

Five calls doesn't work because clients lose patience. By Call 4 they're wondering if you actually know what you're doing, and the budget you were chasing has been reallocated to a competitor who moved faster.

Three calls is the minimum number that produces a defensible scope. Each call has a distinct purpose, and skipping or compressing them is the most common mistake new reps make. This module teaches you what each call is for, who's responsible for what, and how Vega partners with you across the sequence.

## Call 1: Pain identification and data state assessment

The goal of Call 1 is narrow and specific: identify the client's pain points and assess their data maturity. That's it. You're not selling, demoing, scoring workflows, or proposing solutions. You're listening for pain and probing for data state.

The reason for this discipline is that everything downstream depends on the quality of the Call 1 signal. If you misdiagnose data state on Call 1, you'll scope workflows on Call 2 that can't be delivered. If you miss key pain points, Vega will produce a Call 2 agenda that targets the wrong workflows. The whole engagement gets shaky, and you won't recover.

The Call 1 deliverable, in Vega's hands, is a structured analysis: pain points in the client's own words, a data state assessment with justification, a list of stakeholders mentioned, a list of missed questions you should have asked, potential workflows spotted in passing, and a draft agenda for Call 2. Vega produces this from the call transcript after you upload it. You review it, edit anything wrong, and approve it.

The tempting mistake on Call 1 is to start scoping workflows live in front of the client. Resist this. Stay in listening mode. The client will be more impressed by a rep who asks great questions than a rep who jumps to answers.

## Call 2: Scoping and workflow mapping

Call 2 is the deep dive. You walk in with Vega's Call 2 agenda — built from Call 1 findings — and you spend the meeting mapping specific workflows in detail. How does this process actually run today? Who touches it? What systems are involved? How long does it take? How often does it break?

By the end of Call 2, Vega should have enough information to build the AI Employee Matrix: a structured table of every workflow you discussed, with columns for description, source applications, data state, manual hours per week, stakeholders, and a rough MMI score hypothesis. This matrix is the centerpiece of Call 3 and the input to Orion.

Vega also identifies gaps. If during Call 2 you talked about an invoice approval workflow but never established how the approval thresholds actually work, Vega flags that as a gap. If Call 1 mentioned Salesforce but Call 2 mentioned Excel exports, Vega flags the contradiction. These gaps don't get smoothed over — they get surfaced explicitly so you can resolve them.

## The Flexible Decision Loop

Here's where Call 2 gets interesting. Sometimes Vega's gap analysis reveals that you're not actually ready for Call 3. There are too many unresolved questions, or too many contradictions, or too many workflows where critical details are missing.

Vega's response to this is the **Flexible Decision Loop**. Instead of mechanically advancing to Call 3 with TBDs in the matrix, Vega dynamically replans: it generates a follow-up call agenda targeting only the unresolved gaps, and recommends that you schedule that follow-up before moving on. This is not a failure — it's the design working correctly. A follow-up call that closes gaps is dramatically better than a Call 3 that papers over them.

The discipline here is to trust Vega's gap detection. When the loop fires, run the follow-up. Reps who push through to Call 3 anyway end up with weak SOWs that get rejected by the client's procurement team.

## Call 3: MMI scoring and scope lock

Call 3 is the lock-in call. You walk the client through the AI Employee Matrix one final time, confirm the gaps from Call 2 are resolved, and run MMI scoring on each workflow (you'll learn the math in Module 6). By the end of Call 3, the scope is frozen and Vega marks the session ready_for_sow. Orion takes it from there.

Notice that Call 3 is not a sales call. By Call 3, the sale should be implicit. The client has invested time across two prior calls, Vega has produced clear analysis, and the matrix demonstrates concrete value. Your job on Call 3 is to confirm the work, not to convince the client. Reps who try to sell on Call 3 come across as desperate.

## The RACI split

The roles across all three calls follow a clear pattern. Memorize this — it's the structural backbone of the methodology.

**During the live calls**: the Sales Rep / TA is Accountable AND Responsible. You drive the conversation, you ask the questions, you build the rapport. The client is Consulted (you're asking them questions). Vega is Informed (it has access to the transcript, but it's not driving). The reason humans run live calls is simple: building rapport, reading the room, and following emotional threads are not jobs you can hand to an LLM. The technology of live conversation belongs to humans for now, and probably for a long time.

**After the call ends**: the responsibilities flip. Vega becomes Responsible for the analytical work — transcript analysis, gap detection, agenda generation, MMI calculation, matrix building. The Sales Rep / TA stays Accountable for the outcome (you own the deliverable) but Vega is doing the heavy lifting.

This split is sometimes called the EQ vs AQ divide: humans bring Emotional Quotient to live calls, AI brings Analytical Quotient to post-call work. The combination is more powerful than either alone.

## Defensive prompting

One last principle. Vega is built to practice **defensive prompting** — it explicitly fails on ambiguity rather than hallucinating. When Vega can't resolve a question from the transcript alone, it flags the gap for you to address on the next call instead of inventing plausible-sounding details.

This is the most important safety property in the whole methodology. An AI that hallucinates plausible-sounding answers in front of a client is a deal-killer. An AI that says "I don't know, you need to ask this" is a deal-maker, because it forces the rep and the client to confront the gap together. Trust the defensive failures. They're protecting your reputation.
