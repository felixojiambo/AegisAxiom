# SOW Examples for Orion Reference

## Example 1: Mid-Market Property Management AI Implementation

### Executive Summary
This Statement of Work outlines the implementation of three AI Employee workflows for Acme Property Management, addressing manual maintenance triage, vendor invoice processing, and lease renewal optimization. The engagement covers discovery validation, workflow configuration, integration setup, testing, and go-live support over a 12-week timeline.

### Scope of Work

**Workflow 1: Maintenance Triage AI Employee**
- Automated categorization of maintenance requests from AppFolio portal, email, and phone messages
- Urgency assessment using configurable business rules (emergency, urgent, routine, cosmetic)
- Smart vendor routing based on issue type, property location, and vendor availability
- Escalation paths for unresolved or misrouted requests
- Integration points: AppFolio API, email gateway, vendor management system

**Workflow 2: Vendor Invoice Processing AI Employee**
- Automated invoice-to-work-order matching
- Three-way verification: invoice amount vs. work order vs. contract terms
- Exception detection and routing (amount mismatch, missing PO, unapproved vendor)
- Approval routing based on configurable thresholds ($2,500 PM / $10,000 Finance / $10,000+ COO)
- Integration points: QuickBooks API, AppFolio work orders, document storage

**Workflow 3: Lease Renewal Optimization AI Employee**
- Automated renewal identification (90-day lookback)
- Market rate analysis integration for rent adjustment recommendations
- Tenant score calculation based on payment history and tenure
- Offer generation with configurable templates
- Integration points: RentManager lease data, market rate API, email/SMS notification

### Milestones & Timeline
| Milestone | Duration | Deliverable |
|-----------|----------|-------------|
| M1: Discovery Validation | 2 weeks | Confirmed workflow specifications, integration requirements |
| M2: Core Configuration | 3 weeks | AI Employee workflows configured and unit tested |
| M3: Integration Build | 3 weeks | All API integrations live in staging |
| M4: UAT & Training | 2 weeks | User acceptance testing complete, staff trained |
| M5: Go-Live & Hypercare | 2 weeks | Production deployment with dedicated support |

### Pricing
- Platform license: $X,XXX/month (3 AI Employees)
- Implementation services: $XX,XXX one-time
- Integration development: $XX,XXX one-time
- Training: Included in implementation
- Hypercare support (2 weeks post-launch): Included

### Assumptions & Risks
- Client provides API access to AppFolio, QuickBooks, and RentManager within 2 weeks of kickoff
- Existing data is in a usable state (no major data cleanup required)
- Client designates a project champion available for 4-6 hours/week during implementation
- Risk: Third-party API rate limits may require batching for high-volume invoice processing
