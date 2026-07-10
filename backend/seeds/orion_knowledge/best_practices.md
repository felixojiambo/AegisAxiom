# Orion SOW Best Practices

## Common Pitfalls to Avoid

### 1. Scope Creep via Vague Deliverables
Every workflow in the SOW must have measurable acceptance criteria. "Implement AI-powered invoice processing" is too vague. "Automated matching of vendor invoices to work orders with >95% accuracy on structured invoices" is testable.

### 2. Underestimating Integration Complexity
API integrations are the #1 source of timeline slippage. For each integration:
- Confirm API documentation is current and accessible
- Verify authentication method (OAuth, API key, SAML)
- Check rate limits and bulk operation support
- Identify any sandbox/staging environment availability

### 3. Missing Data Quality Assessment
The discovery process should have identified data state (Blind/Siloed/Visible). If data is Siloed or Blind, the SOW MUST include a data preparation milestone before workflow configuration begins.

### 4. Ignoring Change Management
AI Employee adoption requires user training and process change. Every SOW should include:
- At least 2 training sessions (admin + end users)
- Documentation of new vs. old process flows
- A hypercare period post-launch (minimum 2 weeks)

### 5. One-Size-Fits-All Pricing
Tailor pricing to the client's scale. A 50-unit property management company doesn't need the same tier as a 5,000-unit REIT. Use the MMI scores to justify pricing — higher MMI = more value = justified premium.

## SOW Section Guidelines

### Executive Summary
- 2-3 paragraphs max
- Lead with the business problem, not the technology
- Include the key metric each workflow targets (hours saved, error reduction, revenue impact)

### Scope of Work
- One subsection per AI Employee workflow
- Include: trigger, process steps, decision points, integration points, exception handling
- Reference the BPMN diagrams from Vega's analysis

### Milestones
- 5-7 milestones for a typical 3-workflow engagement
- Each milestone: name, duration, deliverable, acceptance criteria
- Include buffer time (add 20% to engineering estimates)

### Pricing
- Separate recurring (platform) from one-time (implementation) costs
- Integration development should be itemized per system
- Include optional line items (additional training, extended hypercare, premium SLA)

### Assumptions & Risks
- List every external dependency (client API access, data availability, stakeholder time)
- For each risk: describe the risk, its likelihood, its impact, and the mitigation plan
- Never assume the client will meet their obligations without documenting them
