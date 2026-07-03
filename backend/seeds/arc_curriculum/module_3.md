# Module 3: The Data Maturity Continuum

## Why this module exists

In Module 2, you learned to spot workflows that look like good AI candidates. In this module, you learn the question that determines whether you can actually deliver on them: *what state is the prospect's data in?*

This matters because AI agents are not magic. They don't read minds, they don't infer context that doesn't exist, and they don't work around missing data. They operate on what's machine-readable and accessible. If a prospect has the perfect AI workflow opportunity but their data lives in a notebook on a shelf, you cannot help them — at least not until they digitize. Knowing this in Call 1 saves you from over-promising in Call 2 and embarrassing yourself in Call 3.

The Data Maturity Continuum gives you three states to listen for: **Blind**, **Siloed**, and **Visible**. By the end of this module, you should be able to diagnose any prospect's state from a 2-minute conversation, and you should know which Power Questions to ask to confirm.

## State 1: Blind

A Blind organization has critical data living in places AI cannot reach. The most common forms are: paper notebooks on desks, instructions in someone's head, decisions that "we just know how to make," and processes that exist only because a long-tenured employee learned them by osmosis.

The textbook signal is a sentence like: *"Marco has been here 15 years and he just knows how dispatch works. We don't really write it down."* Or: *"The pricing logic is in Phil's head. We trained the new rep by sitting next to her for two weeks."*

When you hear this, the honest answer is that you cannot deploy an AI agent in this environment yet. The agent has nothing to read. It cannot infer Marco's dispatch logic from thin air, and any attempt to make it do so will produce hallucinations and failed executions. The professional response is to flag this as a Blind state on Call 1, and on Call 2 propose either a digitization project (move the knowledge into a system of record) or a knowledge-extraction interview process (sit Marco down and document what's in his head) BEFORE you propose any AI agents.

The Power Question for Blind state is: *"If your operations manager were unavailable for a week, where would a temporary replacement find the exact instructions?"* If the answer is "they couldn't" or "they'd have to ask around," you've confirmed Blind. The question works because it forces the prospect to confront the gap between what they think they have documented and what they actually have documented.

This is one of the hardest things to do as a sales rep. Telling a prospect "you're not ready for AI yet" feels like losing the deal. But the alternative — selling them an agent that fails because the underlying data isn't there — loses the deal AND damages your reputation. Honesty here is a long-term win.

## State 2: Siloed

A Siloed organization has digital data, but it's trapped in disconnected applications. The systems exist. They produce machine-readable outputs. But they don't talk to each other, so humans become the integration layer — exporting CSVs from one system, manipulating them in Excel, and importing them into the next.

Bree's deep research doc calls Siloed organizations "functionally handicapped by architectural fragmentation." That's the right phrase. The data is digital, but the connective tissue is missing.

The smoking gun for Siloed is the Excel-as-glue pattern. When a prospect says: *"We use Salesforce for sales, NetSuite for accounting, and Asana for projects — but they don't talk to each other, so we use Excel to track everything across them,"* you're hearing Siloed. Other tells: "we manually export and import," "we have to log into multiple tools to answer one question," "our team spends hours moving data between systems."

The Power Question that reveals Siloed state is: *"How many hours a week does your team spend manually exporting data from one platform to upload it into another?"* Or: *"When you move a project from sales to fulfillment, how does the data physically move between your CRM and your project tool?"* Both of these probe directly for the integration gap that defines the Siloed state.

Siloed prospects are great candidates for AI workflows — but you'll often need to scope an integration phase first, OR design workflows that bridge the silos as part of the solution. Vega will help you think through this in Modules 5 and 7.

## State 3: Visible

A Visible organization has solved the data infrastructure problem. Their data is centralized (often in a warehouse like Snowflake or BigQuery), accessible via APIs, and properly governed with role-based access controls. When you ask a question about their operations, the answer comes from a single source of truth instead of three competing spreadsheets.

The textbook signal: *"Everything flows through our Snowflake warehouse. The CRM, the ERP, the support system — they all sync nightly and we govern access with role-based policies."* Or: *"Our SOPs are in Confluence and our QC team has full API access to our data lake."*

The Power Question for Visible is: *"Do you currently utilize a unified data warehouse where cross-departmental data is aggregated?"* If they say yes and can name the warehouse and explain the sync schedule, you're in Visible territory.

When you find a Visible prospect, you can confidently bypass foundational data engineering and propose complex multi-step agentic workflows immediately. These are your highest-velocity deals. They're also rarer than Siloed prospects, so when you find one, prioritize.

## A trap to avoid

Tool count is not the same as data state. A prospect using 20 SaaS tools but unifying them through an iPaaS like MuleSoft or Workato meets the Visible criteria, even though it sounds chaotic. A prospect using only 3 SaaS tools but with no integration between them is Siloed, even though it sounds simple. Don't let the number of tools fool you — what matters is whether an AI agent has a single coherent surface to query.

## What to do with this information

In Call 1, your job is to listen for data state cues and ask one or two confirming Power Questions. By the end of Call 1, you should have a confident classification: Blind, Siloed, or Visible. This goes into Vega's Call 1 analysis and shapes everything that follows. A misdiagnosis here cascades — you'll scope workflows that can't be delivered, or you'll under-scope a Visible prospect who could absorb much more than you offered.

Get the data state right, and the rest of the discovery process gets dramatically easier.
