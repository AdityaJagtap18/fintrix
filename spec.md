# Specification — Accounts Payable Automation with Human-in-the-Loop

**Feature ID:** 001-ap-automation · **Status:** Draft · **Owner:** Aditya Jagtap, MitraEdge
**Last updated:** 2026-08-28

> This document describes **what** the system does and **why**. It contains no technology
> choices — those live in `plan.md`. Every requirement is numbered and testable so tasks
> and tests can reference it directly.

---

## 1. Problem statement

A mid-market finance team receives supplier invoices by email, post, and vendor portals in
inconsistent formats. Clerks read each one and key roughly fifteen fields into an ERP. The
work is slow, error-prone, and expensive, and it fails in three specific ways:

- **Cost.** Fully loaded cost per manually processed invoice is $6–20 once rework is counted.
- **Late payment.** Backlogs cause missed early-payment discounts and late-payment penalties.
- **Leakage.** Duplicate payments, price variances against the purchase order, and payment
  redirection fraud go undetected because no human compares every invoice to every PO.

Existing OCR tools fail in production because they present extracted values with no
indication of certainty. A field that is 60% likely to be correct looks identical to one
that is 99% likely. Finance teams therefore either check everything by hand — eliminating
the saving — or trust the output and absorb the errors.

## 2. Solution summary

A system that ingests invoices, extracts structured data with per-field confidence scores,
validates them against business rules, matches them against purchase orders and goods
receipts, and then **routes on confidence**. High-confidence, fully validated invoices post
to the ERP without human involvement. Anything uncertain goes to a review queue where a
human confirms only the specific fields in doubt, with those fields highlighted on the
document image. Every correction is captured and becomes training data.

## 3. Glossary

| Term | Definition |
|---|---|
| **STP** | Straight-through processing — an invoice posted to the ERP with no human touch |
| **Exception** | An invoice blocked by a hard validation failure or PO mismatch |
| **Review** | An invoice needing human confirmation of specific low-confidence fields |
| **Golden set** | A fixed corpus of invoices with known-correct values, used to measure accuracy |
| **3-way match** | Reconciliation of invoice against purchase order and goods receipt note |
| **GRN** | Goods receipt note — proof that ordered goods physically arrived |
| **Vendor master** | The authoritative record of approved suppliers and their bank details |
| **Hard rule** | A validation rule whose failure blocks STP |
| **Soft rule** | A validation rule whose failure is surfaced to a reviewer but does not block |
| **Confidence** | Model-reported probability, 0.0–1.0, that an extracted value is correct |

## 4. Personas

| Persona | Goal | Frustration today |
|---|---|---|
| **Priya, AP Clerk** | Clear the queue accurately before cut-off | Keys the same fields all day; blamed for typos |
| **Rajesh, AP Manager** | Know the backlog and where errors come from | No visibility until month-end |
| **Anita, Financial Controller** | No duplicate or fraudulent payments; clean audit | Can only sample-check |
| **Sam, Systems Administrator** | Keep it running, keep costs predictable | Black-box vendor tools with no logs |
| **Auditor (external)** | Trace any posted payment back to its source document | Reconstructs evidence manually |
| **Vendor (external)** | Know the invoice arrived and when it will be paid | Sends invoice into silence, then chases by phone |

## 5. Scope

### In scope
Supplier invoices and credit notes in PDF, TIFF, JPEG, PNG. Ingestion by **WhatsApp**,
monitored mailbox, API upload, and portal drag-and-drop. Multi-invoice PDF splitting.
Extraction, validation, 3-way matching, confidence routing (including shadow mode), human
review, **payment instruction hand-off**, audit trail, correction capture, retraining, an
operational dashboard, and an administration console.

### Out of scope (v1)
**Payment execution.** The system produces a payment instruction; the customer's existing
treasury or banking system executes it. The system never holds banking credentials and never
initiates a transfer. Also out of scope: bank file format generation beyond the agreed ERP
payload, non-invoice document types (statements, remittance advice, contracts), purchase order
creation, approval workflow beyond a single reviewer plus one escalation level, vendor
self-service portal, handwritten invoices, and multi-tenant SaaS hosting — v1 deploys into a
single customer subscription.

> **Why payment execution is excluded.** Initiating payments pulls in banking rails, corporate
> banking API agreements, and maker–checker regulatory controls. Emitting an instruction that
> the customer's existing treasury system executes achieves the same business outcome with none
> of that exposure, and shortens the sales cycle because no bank integration is required to go
> live.

### Explicit non-goals
The system will not attempt to post an invoice it is uncertain about. Increasing STP rate by
lowering thresholds without measured accuracy evidence is a defect, not a feature.

---

## 6. User stories and acceptance criteria

### US-01 — Automatic processing of a clean invoice
**As** Priya, **I want** invoices the system is confident about to post without my involvement,
**so that** I spend my day on genuine exceptions.

- **AC-01.1** Given an invoice whose critical fields all exceed the auto threshold, and which
  passes every hard rule, and whose 3-way match status is MATCHED, when it is processed, then
  it is posted to the ERP with no human interaction and marked STRAIGHT_THROUGH.
- **AC-01.2** Given the same invoice, when processing completes, then end-to-end elapsed time
  from ingestion to ERP hand-off is under 90 seconds at p95.
- **AC-01.3** Given an invoice from a vendor never seen before, when it is processed, then it
  is never posted straight through regardless of confidence.

### US-02 — Targeted field review
**As** Priya, **I want** to be shown only the fields the system is unsure about, with each one
highlighted on the invoice image, **so that** I can resolve an exception in seconds.

- **AC-02.1** Given an invoice where exactly two fields fall below the auto threshold, when
  Priya opens it for review, then exactly those two fields are editable and every other field
  is displayed read-only.
- **AC-02.2** Given a field under review, when it is selected, then the document viewer scrolls
  to the correct page and draws the field's bounding region over the image.
- **AC-02.3** Given a field with a rule-suggested value (for example a recomputed total), when
  the field is shown, then the suggestion is offered as a single-click accept.
- **AC-02.4** Given Priya submits the review, when the submission is accepted, then the corrected
  field's confidence is treated as 1.0 for all downstream routing decisions.

### US-03 — No double-keying
**As** Rajesh, **I want** two clerks to be unable to work the same invoice,
**so that** effort is not wasted and corrections do not conflict.

- **AC-03.1** Given an invoice claimed by Priya, when another reviewer attempts to claim it,
  then the attempt is rejected with a conflict response naming the current holder.
- **AC-03.2** Given an invoice claimed 30 minutes ago with no submission, when the lock expires,
  then the invoice returns to the queue and the abandonment is recorded.
- **AC-03.3** Given a reviewer without the lock, when they submit a review, then the submission
  is rejected.

### US-04 — Duplicate prevention
**As** Anita, **I want** duplicate invoices blocked before they reach the ERP,
**so that** we never pay twice.

- **AC-04.1** Given an invoice whose vendor tax ID and invoice number match a previously
  processed invoice, when it is processed, then it is blocked as an EXCEPTION with reason
  `duplicate_invoice` and is never posted.
- **AC-04.2** Given an invoice matching a prior one on vendor, amount, and date within three
  days but with a different invoice number, when it is processed, then a soft warning is raised
  and the prior document is linked in the review screen.
- **AC-04.3** Given a legitimate duplicate confirmed by a reviewer as distinct, when it is
  approved with an override reason, then it posts and the override is audited.

### US-05 — Payment redirection fraud control
**As** Anita, **I want** any invoice whose payee bank details differ from the vendor master to
require explicit approval, **so that** we do not pay a fraudster.

- **AC-05.1** Given an invoice whose remittance bank account differs from the vendor master
  record, when it is processed, then it is blocked with reason `bank_detail_change` regardless
  of extraction confidence.
- **AC-05.2** Given such an invoice, when a reviewer opens it, then the master value and the
  invoice value are shown side by side.
- **AC-05.3** Given such an invoice, when it is approved, then approval requires a user holding
  the Approver role, not the Reviewer role, and the decision is audited.

### US-06 — Three-way match
**As** Rajesh, **I want** invoices reconciled against the purchase order and goods receipt,
**so that** we do not pay for goods we did not order or receive.

- **AC-06.1** Given an invoice referencing a PO, when unit prices are within the configured
  tolerance and invoiced quantities do not exceed received-and-unbilled quantities, then the
  match status is MATCHED.
- **AC-06.2** Given a unit price exceeding tolerance, when matched, then status is MISMATCH,
  the invoice becomes an EXCEPTION, and the variance is stated in currency and percentage.
- **AC-06.3** Given an invoice quantity exceeding received-and-unbilled quantity, when matched,
  then status is MISMATCH and the shortfall is stated per line.
- **AC-06.4** Given an invoice referencing a closed PO, when matched, then status is MISMATCH.
- **AC-06.5** Given an invoice with no PO reference, when the vendor is configured as
  non-PO-required, then routing continues on confidence alone.

### US-07 — Multi-invoice files
**As** Sam, **I want** a scanned batch containing several invoices split automatically,
**so that** nobody separates PDFs by hand.

- **AC-07.1** Given a PDF containing three invoices, when ingested, then three independent
  invoices are created, each individually routed and each traceable to its source file and
  page range.
- **AC-07.2** Given one segment of a batch fails extraction, when processing completes, then
  the other segments still complete and the failed one is quarantined with a reason.

### US-08 — Corrections improve the system
**As** Rajesh, **I want** the corrections my team makes to improve future accuracy,
**so that** the system gets better instead of staying static.

- **AC-08.1** Given a reviewer changes a field value, when submitted, then a correction record
  is stored containing document reference, field, model value, human value, model identifier,
  model version, and timestamp.
- **AC-08.2** Given at least the configured minimum number of corrections has accumulated, when
  the retraining process runs, then a labelled training dataset is produced from them.
- **AC-08.3** Given a candidate model is trained, when it does not beat the incumbent on the
  golden set, then it is not promoted and the failure is reported.
- **AC-08.4** Given a candidate model beats the incumbent, when promotion is proposed, then a
  human must approve before it becomes active.

### US-12 — Submit an invoice by WhatsApp
**As** a small vendor, **I want** to send my invoice as a WhatsApp photo or PDF and get a
confirmation, **so that** I do not have to use a portal or wonder whether it arrived.

- **AC-12.1** Given a vendor sends a PDF or image to the business WhatsApp number, when it is
  received, then a document is created with the sender's number recorded as the ingestion source.
- **AC-12.2** Given a document is accepted, when ingestion completes, then the vendor receives
  an automatic WhatsApp acknowledgement containing a reference identifier.
- **AC-12.3** Given an invoice reaches a terminal state, when that transition occurs, then the
  vendor receives a status message (accepted for payment, or rejected with a reason).
- **AC-12.4** Given a WhatsApp message contains no attachment or an unsupported file type, when
  it is received, then the vendor receives a message explaining what to send instead.
- **AC-12.5** Given the sending number cannot be matched to a vendor in the vendor master, when
  the document is processed, then it is accepted but flagged as an unrecognised sender.
- **AC-12.6** Outbound messages MUST NOT contain invoice amounts, bank details, or any other
  financial data — only a reference and a status.

### US-13 — Shadow mode before automation
**As** Anita, **I want** to see what the system *would* have decided before I let it decide,
**so that** I can switch on automation based on evidence rather than a vendor's promise.

- **AC-13.1** Given shadow mode is enabled, when an invoice is processed, then it is routed to
  human review regardless of the computed routing decision.
- **AC-13.2** Given shadow mode is enabled, when a routing decision is computed, then the
  decision the system would have taken is recorded alongside the decision actually taken.
- **AC-13.3** Given a reviewer submits a review in shadow mode, when the outcome is compared to
  the shadow decision, then agreement or disagreement is recorded per invoice and per field.
- **AC-13.4** The dashboard MUST report, for any period: how many invoices would have been
  auto-approved, and in how many of those the reviewer changed a value — the would-have-been-wrong count.
- **AC-13.5** Shadow mode MUST be switchable globally and per vendor, and every change MUST be audited.
- **AC-13.6** Given shadow mode is disabled for a vendor, when an invoice from that vendor is
  processed, then confidence routing takes effect normally.

### US-14 — Understandable exceptions
**As** Priya, **I want** an exception explained in plain language rather than a rule code,
**so that** I know what to do without asking anyone.

- **AC-14.1** Given an invoice blocked by any rule or match failure, when a reviewer opens it,
  then a plain-language explanation of the specific discrepancy is displayed, stating the
  values involved and their difference.
- **AC-14.2** The explanation MUST be generated only from data already extracted and validated;
  it MUST NOT introduce any value not present in the invoice, purchase order, or vendor master.
- **AC-14.3** Given explanation generation fails or is unavailable, when the invoice is opened,
  then the underlying rule outcomes are shown instead and review is not blocked.
- **AC-14.4** The explanation is an aid to the reviewer and MUST NOT influence any routing decision.

### US-15 — Administration console
**As** Sam, **I want** a single console for thresholds, vendors, models, and system health,
**so that** operational changes do not require a developer or a portal login.

- **AC-15.1** The console MUST display total invoices processed, accepted, rejected, and in
  progress for a selectable period.
- **AC-15.2** The console MUST display the confidence distribution across processed invoices
  and per extracted field.
- **AC-15.3** The console MUST allow per-vendor thresholds, amount ceilings, PO requirement,
  and shadow mode to be viewed and changed, with every change audited.
- **AC-15.4** The console MUST list model versions with their evaluation scores and allow an
  administrator to promote one.
- **AC-15.5** The console MUST display ingestion channel health and dead-letter queue depth.
- **AC-15.6** All console functions MUST require the Administrator role.

### US-09 — Operational visibility
**As** Rajesh, **I want** a dashboard showing throughput and where exceptions come from,
**so that** I can fix causes rather than symptoms.

- **AC-09.1** The dashboard shows, for a selectable period: STP rate, invoice volume, average
  handling time per reviewed invoice, and exception count by reason.
- **AC-09.2** Exception reasons are rankable by vendor, so a single vendor's layout change is
  visible as a spike.
- **AC-09.3** Cost per processed invoice is displayed and trended.

### US-10 — Audit trail
**As** an auditor, **I want** to reconstruct the full history of any posted invoice,
**so that** I can verify controls were applied.

- **AC-10.1** Given any processed invoice, when its audit trail is requested, then it returns
  the original document, every extracted value with confidence, every rule outcome, the match
  result, the routing decision with reason, every human action with actor and timestamp, and
  the ERP posting payload.
- **AC-10.2** Audit records cannot be modified or deleted through any application interface.
- **AC-10.3** Given a model version was active when an invoice was processed, when the audit is
  requested, then that version identifier is included.

### US-11 — Nothing is lost
**As** Sam, **I want** every document accounted for,
**so that** an invoice never silently disappears.

- **AC-11.1** Given a document that is not an invoice, when classified, then it is quarantined
  with the detected type as the reason and the sender is notified.
- **AC-11.2** Given a corrupt or password-protected file, when ingestion fails, then it is
  quarantined, not retried indefinitely.
- **AC-11.3** Given a transient service failure, when retries are exhausted, then the message
  is dead-lettered and an alert is raised.
- **AC-11.4** Given an invoice in review with no action for 48 hours, when the deadline passes,
  then it is escalated to the manager and remains in the queue.

---

## 7. Functional requirements

### Ingestion
- **FR-001** The system MUST accept documents from a WhatsApp business number, a monitored
  mailbox, an authenticated HTTP upload endpoint, and a watched storage location.
- **FR-001a** The system MUST send an automatic acknowledgement to a WhatsApp sender on
  acceptance, and a status message on reaching a terminal state. **Req:** AC-12.2, AC-12.3.
- **FR-001b** Outbound vendor messages MUST contain only a reference identifier and a status;
  they MUST NOT contain amounts, bank details, or other financial data. **Req:** AC-12.6.
- **FR-001c** The system MUST resolve an inbound WhatsApp sender number against the vendor
  master and flag unrecognised senders without rejecting the document. **Req:** AC-12.5.
- **FR-002** The system MUST accept PDF, TIFF, JPEG, PNG, BMP, and HEIF up to 500 MB and 2000 pages.
- **FR-003** The system MUST compute a content hash on ingestion and, if that hash was processed
  within the retention window, reuse the stored extraction result rather than re-extracting.
- **FR-004** The system MUST record ingestion source, original filename, sender identity where
  available, and received timestamp for every document.
- **FR-005** The system MUST reject and quarantine files it cannot open, without retrying.

### Classification and splitting
- **FR-010** The system MUST classify each document before extraction and quarantine anything
  not classified as an invoice or credit note.
- **FR-011** The system MUST detect documents containing multiple invoices and split them into
  separately routed invoices, each retaining a reference to source file and page range.
- **FR-012** The system MUST select the extraction model based on the classification result and
  vendor-specific configuration where one exists.

### Extraction
- **FR-020** The system MUST extract, at minimum: vendor name, vendor address, vendor tax ID,
  customer name, invoice number, invoice date, due date, purchase order number, currency,
  subtotal, tax total, invoice total, amount due, payment terms, and remittance details.
- **FR-021** The system MUST extract line items with description, quantity, unit price, line
  amount, tax rate, and product code where present.
- **FR-022** Every extracted field MUST carry a confidence score between 0.0 and 1.0 and the
  page number and bounding region where the value was found.
- **FR-023** Every extracted line item MUST carry a row-level confidence score.
- **FR-024** The system MUST retain the complete raw extraction response so that validation and
  routing can be re-run without re-invoking the billed extraction service.
- **FR-025** The system MUST normalise dates to ISO 8601, amounts to decimal with two places,
  and currency codes to ISO 4217 before validation.

### Validation
- **FR-030** The system MUST evaluate all rules in the catalogue (Section 8) on every invoice
  and record each outcome.
- **FR-031** A hard rule failure MUST prevent straight-through processing.
- **FR-032** A soft rule failure MUST be surfaced to the reviewer without blocking.
- **FR-033** Where a rule can compute a correct value, it MUST offer that value as a suggestion.
- **FR-034** The rule catalogue MUST be extensible without modifying the routing logic.

### Matching
- **FR-040** The system MUST attempt to resolve a purchase order from the extracted PO number
  and, failing that, from vendor and total amount.
- **FR-041** The system MUST compare unit prices against PO prices within a configurable
  percentage tolerance.
- **FR-042** The system MUST compare invoiced quantities against received-but-not-yet-invoiced
  quantities and flag over-invoicing.
- **FR-043** The system MUST support vendors configured as not requiring a PO.
- **FR-044** Match tolerances MUST be configurable globally and overridable per vendor.

### Routing
- **FR-050** The system MUST classify every invoice into exactly one of: STRAIGHT_THROUGH,
  REVIEW, FULL_REVIEW, EXCEPTION, REJECTED.
- **FR-051** Auto and review thresholds MUST be configurable per vendor.
- **FR-052** A vendor with no processing history MUST NOT be eligible for straight-through
  processing until a configurable number of invoices have been successfully reviewed.
- **FR-053** The system MUST support a per-vendor maximum amount above which straight-through
  processing is disallowed regardless of confidence.
- **FR-054** A field corrected by a human MUST be treated as fully confident in routing.
- **FR-055** Every routing decision MUST record a machine-readable reason code.
- **FR-056** The system MUST support **shadow mode**, in which the routing decision is computed
  and recorded but every invoice is sent to human review regardless. **Req:** AC-13.1, AC-13.2.
- **FR-057** Shadow mode MUST be configurable globally and per vendor, and every change audited. **Req:** AC-13.5.
- **FR-058** In shadow mode the system MUST record, per invoice, whether the human outcome
  agreed with the shadow decision, and which fields the reviewer changed. **Req:** AC-13.3.
- **FR-059** The system MUST report shadow-mode agreement statistics: invoices that would have
  been auto-approved, and how many of those the reviewer corrected. **Req:** AC-13.4.

### Human review
- **FR-060** The system MUST present a queue filterable by decision type, vendor, age, and amount.
- **FR-061** A reviewer MUST claim an invoice before editing it, and a claim MUST be exclusive.
- **FR-062** A claim MUST expire after a configurable idle period and return the invoice to the queue.
- **FR-063** The review screen MUST display the document image alongside extracted values and
  highlight the region of the currently selected field.
- **FR-064** Only fields flagged for review MUST be editable; all others MUST be read-only but
  visible.
- **FR-065** A reviewer MUST be able to approve or reject; rejection MUST require a reason.
- **FR-066** Approval of an invoice flagged for bank detail change MUST require the Approver role.
- **FR-067** Invoices unreviewed after a configurable period MUST escalate without leaving the queue.
- **FR-068** The review screen MUST display a plain-language explanation of any rule or match
  failure, stating the values involved and their difference. **Req:** AC-14.1.
- **FR-069** Explanations MUST be derived solely from extracted and validated data, MUST NOT
  affect routing, and MUST degrade to raw rule outcomes if generation is unavailable. **Req:** AC-14.2..14.4.

### Learning loop
- **FR-070** Every field correction MUST be persisted with model value, human value, model
  identifier and version, field name, document reference, actor, and timestamp.
- **FR-071** The system MUST be able to generate a labelled training dataset from accumulated
  corrections.
- **FR-072** A newly trained model MUST be evaluated against the golden set before use.
- **FR-073** A new model MUST NOT become active without explicit human approval.
- **FR-074** The active model version MUST be recorded on every processed invoice.

### Payment instruction hand-off
- **FR-080** The system MUST emit a structured **payment instruction** for every approved
  invoice, containing vendor reference, amount, currency, due date, invoice reference, and
  approval evidence.
- **FR-081** Hand-off MUST be idempotent — the same invoice MUST NOT be handed off twice even
  if the operation is retried or the orchestration replays.
- **FR-082** A failed hand-off MUST be retried with backoff and, on exhaustion, raise an alert
  and move the invoice to a failed state that a human can resolve.
- **FR-083** The hand-off target MUST be replaceable without changes to core processing.
- **FR-084** The system MUST NOT initiate, authorise, or execute any financial transfer, and
  MUST NOT store banking credentials of the customer or any vendor.
- **FR-085** The payment instruction MUST reference the vendor's bank details held in the
  customer's vendor master rather than values extracted from the invoice.

### Administration
- **FR-095** The system MUST provide an administration console restricted to the Administrator role.
- **FR-096** The console MUST allow per-vendor thresholds, amount ceiling, PO requirement, and
  shadow mode to be changed, with each change audited. **Req:** AC-15.3.
- **FR-097** The console MUST display processed, accepted, rejected, and in-progress counts, and
  the confidence distribution overall and per field. **Req:** AC-15.1, AC-15.2.
- **FR-098** The console MUST list model versions with evaluation scores and support promotion. **Req:** AC-15.4.
- **FR-099** The console MUST display ingestion channel health and dead-letter queue depth. **Req:** AC-15.5.

### Audit and reporting
- **FR-090** The system MUST maintain an append-only audit log of every state transition and
  human action.
- **FR-091** The system MUST expose an endpoint returning the complete processing history of a
  single invoice.
- **FR-092** The system MUST expose aggregate metrics: STP rate, volume, handling time,
  exception reasons, cost per invoice.
- **FR-093** Audit records MUST be retained for a configurable period of at least seven years.

---

## 8. Business rule catalogue

| ID | Rule | Severity | Suggests a value |
|---|---|---|---|
| BR-01 | Required fields present: vendor, invoice number, invoice date, total | HARD | No |
| BR-02 | Line item amounts sum to subtotal within tolerance | HARD | Yes |
| BR-03 | Subtotal plus tax equals total within tolerance | HARD | Yes |
| BR-04 | Exact duplicate on vendor tax ID and invoice number | HARD | No |
| BR-05 | Near-duplicate on vendor, amount, date ±3 days | SOFT | No |
| BR-06 | Invoice date is not in the future | HARD | No |
| BR-07 | Invoice date is within the accepted age window | SOFT | No |
| BR-08 | Due date is on or after invoice date | HARD | Yes |
| BR-09 | Tax identifier passes format and checksum validation for its jurisdiction | HARD | No |
| BR-10 | Currency is in the approved list | HARD | No |
| BR-11 | Vendor resolves to the vendor master | HARD | Yes |
| BR-12 | Vendor name fuzzy match scores at least the configured threshold | SOFT | Yes |
| BR-13 | Payee bank details match the vendor master | HARD | No |
| BR-14 | Tax rate is plausible for the vendor's jurisdiction and history | SOFT | No |
| BR-15 | Total does not exceed the vendor's configured maximum invoice value | SOFT | No |
| BR-16 | Line item quantities and unit prices are non-negative (credit notes excepted) | HARD | No |

**Tolerance defaults:** amount ±1.00 base currency unit; unit price ±2%; quantity ±0%;
name fuzzy match ≥88; invoice age ≤365 days.

---

## 9. Invoice state machine

```
RECEIVED ──► CLASSIFIED ──► EXTRACTED ──► VALIDATED ──► MATCHED ──► ROUTED
    │             │              │                                    │
    │             │              │                    ┌───────────────┼───────────────┐
    │             │              │                    ▼               ▼               ▼
    │             │              │            STRAIGHT_THROUGH   IN_REVIEW      EXCEPTION
    │             │              │                    │               │               │
    │             │              │                    │        ┌──────┴──────┐        │
    │             │              │                    │        ▼             ▼        │
    │             │              │                    │    APPROVED      REJECTED     │
    │             │              │                    │        │             │        │
    │             │              │                    └────────┼─────────────┘        │
    │             │              │                             ▼                      │
    │             │              │                         HANDOFF ──► RELEASED         │
    │             │              │                             │                      │
    │             │              │                             ▼                      │
    │             │              │                       HANDOFF_FAILED               │
    ▼             ▼              ▼                                                    ▼
QUARANTINED  QUARANTINED   QUARANTINED                                          IN_REVIEW
```

**Terminal states:** RELEASED, REJECTED, QUARANTINED.
**Rules:** transitions are recorded in the audit log with actor and timestamp. RELEASED is
reachable only from APPROVED or STRAIGHT_THROUGH. An EXCEPTION may be moved to IN_REVIEW by a
human but never directly to RELEASED. RELEASED means a payment instruction was handed off, not
that money moved — execution belongs to the customer's treasury system (FR-084).

---

## 10. Non-functional requirements

### Performance
- **NFR-001** p95 end-to-end latency for a 2-page invoice from ingestion to routing decision
  MUST be under 90 seconds.
- **NFR-002** The system MUST sustain 500 invoices per hour without queue growth.
- **NFR-003** The review queue MUST return the first page of results in under 500 ms at p95.
- **NFR-004** The document image MUST render in the review screen within 2 seconds at p95.

### Reliability
- **NFR-010** Monthly availability of the review interface MUST be at least 99.5%.
- **NFR-011** No invoice may be lost: every ingested document reaches a terminal state or an
  alerting failure state.
- **NFR-012** The system MUST tolerate a complete extraction-service outage of up to four hours
  by queueing, with automatic drain on recovery.
- **NFR-013** Recovery point objective 24 hours; recovery time objective 4 hours.

### Security
- **NFR-020** All data MUST be encrypted in transit (TLS 1.2 minimum) and at rest.
- **NFR-021** Authentication MUST use the corporate identity provider; no local accounts.
- **NFR-022** Authorisation MUST distinguish at minimum: Reviewer, Approver, Administrator,
  Auditor (read-only).
- **NFR-023** No service credential may be stored in source, configuration, or CI.
- **NFR-024** Document access URLs MUST be short-lived and user-scoped.
- **NFR-025** Personal data appearing in documents MUST NOT be written to application logs.

### Compliance and data residency
- **NFR-030** All processing and storage MUST remain in the customer's chosen geography.
- **NFR-031** The system MUST support configurable retention and automated purge per document class.
- **NFR-032** The system MUST support a fully on-premises extraction deployment for customers
  whose documents cannot leave their network.

### Cost
- **NFR-040** Cost per processed invoice MUST be measured and reported.
- **NFR-041** Duplicate content MUST NOT incur a second extraction charge within the cache window.
- **NFR-042** Blank and separator pages MUST be excluded before billed extraction.
- **NFR-043** A monthly budget threshold MUST raise an alert at 80% of forecast.

### Maintainability
- **NFR-050** Replacing the extraction engine MUST require changes to one adapter class only.
- **NFR-051** Replacing the ERP target MUST require changes to one adapter class only.
- **NFR-052** Adding a validation rule MUST require no change to routing logic.
- **NFR-053** Core business logic MUST be testable without network access or cloud credentials.

---

## 11. Success metrics

| Metric | Baseline | Target at 3 months | How measured |
|---|---|---|---|
| Straight-through processing rate | 0% | ≥ 80% | routing decisions / total |
| Shadow-mode agreement before go-live | — | ≥ 99% on would-auto-approve set | shadow decision vs human outcome |
| Field accuracy on critical fields | — | ≥ 96% | golden set macro F1 |
| Average handling time per exception | 8 min | ≤ 60 sec | claim to submit elapsed |
| Duplicate payments | unknown | 0 | BR-04 blocks / audit |
| Cost per invoice | $6–20 | ≤ $0.50 infrastructure | metered cost / volume |
| Invoices lost or unaccounted | unknown | 0 | ingested vs terminal state |

---

## 12. Assumptions

- **A-01** The customer can export vendor master, open purchase orders, and goods receipts at
  least daily. Real-time integration is not required for v1.
- **A-02** Invoices are machine-generated or clearly scanned; handwritten invoices are out of scope.
- **A-03** The customer has an identity provider that supports OIDC.
- **A-04** The ERP or treasury system accepts a structured payload over an API or file drop;
  direct database writes are not required, and it — not this system — executes the payment.
- **A-05** At least 100 historical invoices with known-correct values are available to seed the
  golden set.
- **A-06** The customer holds, or will obtain, a Meta Business account and an approved WhatsApp
  Business number. Approval typically takes several days and must be started before Phase 2.
- **A-07** Vendors are willing to send invoices by WhatsApp. It is one supported channel among
  several, never the only route in.

## 13. Open questions

| ID | Question | Blocks | Owner |
|---|---|---|---|
| Q-01 | Which ERP is the first target, and what is its posting contract? | FR-080 | Customer |
| Q-02 | Are credit notes processed in v1 or deferred? | BR-16, FR-010 | Product |
| Q-03 | What is the approval hierarchy above the amount ceiling? | FR-066 | Customer finance |
| Q-04 | Which jurisdictions' tax IDs must be validated at launch? | BR-09 | Customer |
| Q-05 | Is on-premises extraction required at launch or is cloud acceptable? | NFR-032 | Customer IT |
| Q-06 | How long must shadow mode run before automation may be switched on? | FR-056 | Customer finance |
| Q-07 | Which treasury or ERP system consumes the payment instruction, in what format? | FR-080 | Customer |
| Q-08 | Is a WhatsApp Business number already approved, or must one be obtained? | FR-001 | Customer |

Every open question MUST be resolved before the task it blocks is started. Do not resolve
them by guessing — an unresolved question is a valid reason to stop.
