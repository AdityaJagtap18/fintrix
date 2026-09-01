# Technical Plan — 001-ap-automation

**Implements:** `spec.md` · **Governed by:** `.specify/memory/constitution.md`
**Owner:** Aditya Jagtap, MitraEdge

> This document decides **how**. Every choice below must trace to a requirement in `spec.md`
> and comply with the constitution. Where a choice is contested, the rejected alternative and
> the reason are stated so the decision can be revisited rather than rediscovered.

---

## 1. Technology decisions

**Rule for this table:** every service listed must have a one-line reason naming the requirement
it satisfies. A service with no requirement behind it does not enter the architecture — breadth
without justification reads as collecting, not engineering.

| Layer | Choice | Satisfies | Rejected alternative and why |
|---|---|---|---|
| WhatsApp intake | Azure Communication Services — Advanced Messaging | FR-001, FR-001a | Twilio — works, but a non-Azure dependency and a second billing relationship |
| Email intake | Logic Apps + Office 365 connector | FR-001 | Graph subscription in code — more to maintain for no gain |
| API intake | API Management fronting the upload endpoint | FR-001, NFR-021 | Direct exposure — no rate limiting, no centralised auth |
| PII redaction | Azure AI Language | NFR-025 | Regex — misses names and addresses in free-text fields |
| Exception explanations | Azure OpenAI | FR-068, FR-069 | Templated strings — brittle across rule combinations, reads robotic |
| UI hosting | Static Web Apps | FR-095, NFR-021 | Serving React from the API container — couples frontend releases to backend |
| Security posture | Defender for Cloud | NFR-020, NFR-023 | Manual review — not continuous, not demonstrable to an auditor |
| Extraction | Azure AI Document Intelligence v4.0 (`2024-11-30` GA) | FR-020..024 | Content Understanding — better multimodal, but lower deterministic accuracy on templated invoices and smaller file limits; revisit per ADR-0001 |
| Language | Python 3.11 | — | C# is equally valid; Python chosen for the labelling/eval tooling ecosystem |
| Orchestration | Durable Functions (Python) | FR-011, NFR-011, NFR-012 | Logic Apps — poor testability, hard to version; plain queues — no checkpointed state |
| Review API | FastAPI on Container Apps | FR-060..067 | App Service — no scale-to-zero; AKS — operational overhead unjustified at this scale |
| Review & admin UI | React + Vite + PDF.js | FR-063, FR-095 | Server-rendered — bounding-box overlay needs client-side canvas control |
| Eventing | Event Grid (blob) + Service Bus (work queues) | NFR-011 | Storage Queues — no dead-lettering or sessions |
| Transactional store | Cosmos DB (NoSQL API, serverless) | FR-090, US-06 | Azure SQL — GP_S_Gen5 still carries an always-on `minCapacity` floor in prod; Cosmos serverless has none, pure pay-per-request, better fit for this traffic profile |
| Document store | Blob Storage with lifecycle tiering | NFR-041, NFR-031 | — |
| Identity | Microsoft Entra ID, Managed Identity | NFR-021..023 | — |
| Secrets | Key Vault; no secrets in CI | Article III | — |
| Telemetry | App Insights + OpenTelemetry | NFR-040, AC-09.1 | — |
| IaC | Bicep | Article IX | Terraform equally valid; Bicep chosen for first-party Azure schema fidelity |
| CI/CD | GitHub Actions with OIDC federation | Article III, IV | — |

### Deliberately excluded

| Service | Why not |
|---|---|
| Azure SQL | Was the original choice — reversed for cost/ops (see decision table above). The "invoice/line/PO/GRN/vendor all join" concern that justified SQL turned out narrower on inspection: `three_way.match_invoice()` already receives a pre-assembled `PurchaseOrder` with its `.lines` populated — assembling that is the repository's job either way, and Cosmos's embed-PO-lines-and-goods-receipts-in-one-document pattern makes it a single point read, not a workaround. `vendor.resolve_vendor()`'s fuzzy matching already runs in Python (RapidFuzz) over the candidate pool, not a DB-side trigram index, so nothing is lost there either. What Cosmos genuinely gives up: DB-enforced foreign keys (now app-only), and cross-entity reporting aggregates become cross-partition queries — acceptable at this project's stated "thousands of rows" volume. |
| Data Factory | Vendor master and PO ingestion is a daily CSV. A Function does it in twenty lines. |
| AKS | Container Apps covers the scaling need. Kubernetes here is overhead with no requirement behind it. |
| Synapse / Fabric | Reporting volume is thousands of rows. Cross-partition Cosmos queries answer every dashboard query at this scale. |
| Cache for Redis | The content-hash cache lives in the `documents` container, partitioned by `content_hash` itself — a point read, read once per document. No hot path justifies a second cache. |

Be ready to give these answers out loud. Naming what you left out, and why, is a stronger
signal than the length of the list of what you put in.

## 2. Component architecture

```
src/
├── core/                        ← pure domain, no SDK imports (Article I)
│   ├── models.py                Invoice, ExtractedField, BoundingRegion, LineItem,
│   │                            ValidationIssue, MatchResult, RoutingResult, enums
│   ├── extraction/
│   │   ├── base.py              DocumentExtractor ABC, ExtractionRequest, error types
│   │   └── document_intelligence.py   ← the only file importing the DI SDK
│   ├── validation/
│   │   ├── rules.py             rule registry, ValidationContext, all BR-xx rules
│   │   └── normalise.py         dates → ISO 8601, amounts → Decimal, currency → ISO 4217
│   ├── matching/
│   │   ├── three_way.py         PO/GRN reconciliation
│   │   └── vendor.py            vendor master resolution, fuzzy name matching
│   ├── routing/
│   │   └── confidence_router.py RoutingPolicy, route()
│   └── persistence/             repository protocols only — no implementations
├── infrastructure/              ← adapters implementing core protocols
│   ├── repositories/            Cosmos DB implementations
│   ├── blob.py, servicebus.py
│   ├── payments/                PaymentInstructionAdapter ABC + ERP/file adapters
│   ├── messaging/                ACS WhatsApp send and receive
│   └── explain/                  Azure OpenAI exception explainer
├── functions/                   ← Azure Functions host
│   ├── ingest/                  Event Grid + HTTP triggers
│   ├── orchestrator/            Durable orchestrator (deterministic, IO-free)
│   └── activities/              one module per activity, thin wrappers over core
├── api/                         ← FastAPI
│   ├── main.py, dependencies.py, security.py
│   └── routers/                 review, documents, metrics, audit, admin
└── web/                         ← React review UI
```

**Dependency rule:** `core` depends on nothing internal. `infrastructure`, `functions`, and
`api` depend on `core`. Nothing depends on `functions` or `api`. Enforced by an import-linter
rule in CI.

## 3. Data model

The field lists below are the domain shape — they don't change with the storage engine.
What changes is *how* they're stored: Cosmos DB, one container per entity below, mapped
from these same fields. Container names, partition keys, and where SQL's join-time tables
(`invoice_fields`, `invoice_lines`, `validation_results`, `purchase_order_lines`,
`goods_receipts`) become embedded arrays instead of separate joined tables:

| Container | Partition key | Embeds |
|---|---|---|
| `vendors` | `/vendor_id` | — |
| `vendor_tax_ids` (lookup-only, reproduces global `tax_id` uniqueness) | `/tax_id` | — |
| `documents` | `/content_hash` | — |
| `invoices` | `/vendor_id` (sentinel `"unresolved"` pre-resolution) | `invoice_fields`, `invoice_lines`, `validation_results` |
| `purchase_orders` | `/vendor_id` | `purchase_order_lines`, `goods_receipts` |
| `review_assignments` | `/invoice_id` | — |
| `corrections` | `/invoice_id` | — |
| `audit_log` | `/entity_id` | — |
| `model_registry` | `/model_id` | — |

Defined in `infra/modules/cosmos.bicep`, which also carries the per-container RBAC.

### `vendors`
`vendor_id` PK · `name` · `normalised_name` · `tax_id` (unique, via `vendor_tax_ids`) ·
`address` · `bank_account` · `currency` · `requires_po` · `auto_threshold` ·
`review_threshold` · `max_auto_amount` · `invoices_processed` · `is_active` · timestamps

### `documents`
`document_id` PK · `content_hash` (partition key — FR-003's dedupe lookup is a point read) ·
`source` (email/api/blob) · `original_filename` · `sender` · `blob_uri` · `page_count` ·
`received_at` · `parent_document_id` (nullable, for splits) · `page_range` · `status`

### `invoices`
`invoice_id` PK · `document_id` FK · `vendor_id` (partition key) FK nullable ·
`invoice_number` · `invoice_date` · `due_date` · `po_number` · `currency` · `subtotal` ·
`tax_total` · `total` · `amount_due` · `state` · `routing_decision` · `routing_reason` ·
`overall_confidence` · `model_id` · `model_version` · `extracted_at` · `posted_at`
**Unique key policy:** `/invoice_number`, scoped to the `vendor_id` partition — BR-04
enforced at the database level, not only in code, same as the SQL filtered unique index it
replaces.

### `invoice_fields`
`id` PK · `invoice_id` FK · `field_name` · `value` · `confidence` · `page` · `polygon` (JSON) ·
`source` (model/human/rule/default) · `needs_review`

### `invoice_lines`
`id` PK · `invoice_id` FK · `line_number` · `description` · `quantity` · `unit_price` ·
`amount` · `tax_rate` · `product_code` · `row_confidence` · `matched_po_line_id` nullable

### `validation_results`
`id` PK · `invoice_id` FK · `rule_id` · `severity` · `passed` · `message` · `fields` (JSON) ·
`suggested_value`

### `purchase_orders` / `purchase_order_lines` / `goods_receipts`
Mirrored from the customer's ERP export. PO lines carry `quantity_ordered`,
`quantity_received`, `quantity_invoiced`, `unit_price`, `product_code`, `is_closed`. Lines
and goods receipts are embedded in the `purchase_orders` document (partitioned by
`vendor_id`), so `three_way.match_invoice()`'s already-assembled `PurchaseOrder` object is a
single point read, not a join.

### `review_assignments`
`invoice_id` PK · `claimed_by` · `claimed_at` · `expires_at` · `submitted_at` · `outcome`

### `corrections`
`id` PK · `invoice_id` FK · `field_name` · `model_value` · `human_value` · `model_id` ·
`model_version` · `corrected_by` · `corrected_at` · `used_in_training_run` nullable

### `audit_log` (append-only)
`id` PK · `entity_type` · `entity_id` (partition key) · `action` · `actor` · `actor_role` ·
`before` (JSON) · `after` (JSON) · `reason` · `occurred_at`
**Enforcement:** a custom Cosmos RBAC role (`audit-log-writer` in `cosmos.bicep`) grants the
application identity `create` and `read` data actions only — no `replace`, `upsert`, or
`delete` — the same guarantee the SQL `DENY UPDATE/DELETE` grant gave.

### `model_registry`
`model_id` PK · `alias` (prod/candidate) · `trained_at` · `training_run_id` · `macro_f1` ·
`document_exact_match` · `promoted_by` · `promoted_at` · `is_active`

## 3a. Ingestion channels

| Channel | Mechanism | Notes |
|---|---|---|
| WhatsApp | ACS Advanced Messaging webhook → Functions HTTP trigger | verify webhook signature; download media to `raw/`; sender number recorded as source |
| Email | Logic App polling the AP mailbox | attachments to `raw/`; body kept for context only |
| API | APIM → Container Apps upload endpoint | Entra auth, rate limited, 500 MB cap |
| Portal | Static Web App drag-and-drop → same upload endpoint | |
| Watched folder | Blob container + Event Grid | SFTP drops from large vendors |

All five converge on the same blob container and the same Event Grid event, so there is exactly
one processing path regardless of how a document arrived. `document.source` records which.

**Outbound vendor messaging.** ACS sends acknowledgement and final-status messages. Templates
carry a reference and a status only — never an amount or bank detail (FR-001b). Outbound
messages are queued and retried; a delivery failure never blocks invoice processing.

## 3b. Shadow mode

Shadow mode is how automation gets switched on with evidence instead of a promise. The router
always computes its decision; a flag decides whether that decision is acted on.

```
decision = route(invoice, issues, match, policy)

if shadow_mode_enabled(vendor):
    record shadow_decision = decision
    actual_decision = REVIEW              # every invoice still sees a human
else:
    actual_decision = decision
```

On review submission the system records whether the human agreed with the shadow decision, and
which fields were changed. The dashboard then reports the sentence that sells the switch-on:
*"of 4,000 invoices your team reviewed, we would have auto-approved 3,300 and been wrong on 11."*

**Resolves:** FR-056..059, AC-13.1..13.6. **Default:** enabled. Disabling it for a vendor is an
audited administrative action, never a deployment.

## 3c. Exception explanations

When an invoice is blocked, the reviewer sees prose rather than `three_way_match:mismatch`.

- **Input:** only the structured rule outcomes, extracted values, PO line, and vendor master
  record already in hand. No free text from the document, no retrieval, no external lookup.
- **Constraint:** the prompt instructs the model to state only the values supplied and to
  produce nothing if the data is insufficient. Every number in the output must be traceable to
  an input field, and the response is validated against that before display (FR-069).
- **Failure mode:** on timeout or error, raw rule outcomes render instead. Review is never
  blocked by this feature (AC-14.3).
- **Boundary:** explanation runs after routing and cannot influence it (AC-14.4). This matters —
  a generative model must never sit in the decision path for a payment.
- **Cost:** one short call per exception, never per invoice.

## 4. Processing pipeline

| # | Stage | Activity | Retry | Notes |
|---|---|---|---|---|
| 1 | Ingest | `ingest_document` | none | hash, dedupe check (FR-003), page count, quarantine unopenable (FR-005) |
| 2 | Classify | `classify_document` | 5× cheap | quarantine non-invoices (FR-010) |
| 3 | Split | `split_document` | 3× | fan out to sub-orchestrations (FR-011) |
| 4 | Extract | `extract_invoice` | 3× transient only | billed — never retry on 4xx (Article XII) |
| 5 | Normalise | `normalise_invoice` | none | pure, in-process (FR-025) |
| 6 | Resolve vendor | `resolve_vendor` | 5× | tax ID exact, then fuzzy name ≥88 |
| 7a | Validate | `validate_invoice` | none | parallel with 7b |
| 7b | Match | `three_way_match` | 5× | parallel with 7a |
| 8 | Route | `route_invoice` | none | pure |
| 9 | Persist | `persist_result` | 5× | idempotent on `document_id` (Article V) |
| 10a | Hand off | `emit_payment_instruction` | 10× | STP path only; idempotency key = `invoice_id`; instruction only, never executes payment (FR-084) |
| 10b | Enqueue review | `enqueue_for_review` | 5× | then `wait_for_external_event("ReviewCompleted")` with 48h escalation timer (AC-11.4) |
| 11 | Record | `record_corrections` | 5× | learning loop (FR-070) and shadow agreement (FR-058) |
| 12 | Notify | `notify_vendor` | 5× | WhatsApp status; reference and status only (FR-001b) |

**Concurrency:** `maxConcurrentActivityFunctions` tuned to stay below the extraction service
rate limit. Throttling responses trigger exponential backoff, never a tight retry loop.

## 5. Confidence routing algorithm

```
critical  = {vendor_name, invoice_number, invoice_date, total, currency}
important = {subtotal, tax_total, due_date, po_number}
confidence(f) = 1.0 if field source is "human" else model confidence   # FR-054
overall = min(confidence(f) for f in critical)

if shadow_mode_enabled(vendor)                   → compute below, record as shadow, return REVIEW
if any hard rule failed                          → EXCEPTION  (validation:<rule_id>)
elif match is MISMATCH and vendor requires PO    → EXCEPTION  (three_way_match:mismatch)
elif match is PARTIAL                            → REVIEW     (three_way_match:partial)
elif vendor.invoices_processed < min_history     → REVIEW     (policy:new_vendor)
elif total > vendor.max_auto_amount              → REVIEW     (policy:amount_ceiling)
elif overall ≥ auto AND no low field AND no low line → STRAIGHT_THROUGH
elif overall ≥ review                            → REVIEW     (confidence:field_review)
else                                             → FULL_REVIEW
```

Thresholds resolve in order: vendor override → global config → code default
(auto 0.92, review 0.70, line 0.80).

## 6. API surface

```
POST   /api/v1/documents                    upload (FR-001)
GET    /api/v1/documents/{id}/content       short-lived user-delegated URL (NFR-024)

GET    /api/v1/review/queue                 filterable (FR-060)
POST   /api/v1/review/{id}/claim            exclusive lock (FR-061, AC-03.1)
POST   /api/v1/review/{id}/release
POST   /api/v1/review/{id}/submit           approve/reject + corrections (FR-065)

GET    /api/v1/invoices/{id}
GET    /api/v1/invoices/{id}/audit          full history (FR-091, AC-10.1)

GET    /api/v1/metrics/summary              STP, AHT, exceptions, cost (FR-092)
GET    /api/v1/metrics/exceptions           grouped by reason and vendor (AC-09.2)

GET    /api/v1/admin/vendors/{id}/policy    thresholds
PUT    /api/v1/admin/vendors/{id}/policy    audited change (Article VIII)
GET    /api/v1/admin/models                 registry
POST   /api/v1/admin/models/{id}/promote    Administrator role only (FR-073)
GET    /api/v1/admin/overview               processed/accepted/rejected, confidence bands (FR-097)
GET    /api/v1/admin/shadow                 would-have-approved vs corrected (FR-059)
PUT    /api/v1/admin/vendors/{id}/shadow    toggle shadow mode, audited (FR-057)
GET    /api/v1/admin/channels               ingestion channel health, DLQ depth (FR-099)

POST   /api/v1/webhooks/whatsapp            ACS inbound, signature-verified (FR-001)

GET    /health/live   GET /health/ready
```

**Auth:** Entra ID bearer tokens; app roles `Invoice.Reviewer`, `Invoice.Approver`,
`Invoice.Administrator`, `Invoice.Auditor` mapped to endpoint scopes (NFR-022).

## 7. Testing strategy

| Level | Scope | Runs | Gate |
|---|---|---|---|
| Unit | `core/` — rules, router, matching, normalisation | every PR, no network | ≥90% coverage on core |
| Contract | API schema, ERP adapter contract | every PR | schema diff must be intentional |
| Integration | activities against sandbox services | every PR | must pass |
| Golden set | 50+ real invoices, field-level F1 | PRs touching extraction | macro F1 drop >0.01 fails (Article IV) |
| E2E smoke | upload → route → review → post | post-deploy | must pass before prod promotion |
| Load | 500 invoices/hour sustained | weekly | queue depth must not grow (NFR-002) |

**Golden set composition:** at least 10 vendors, at least 5 poor-quality scans, at least 3
multi-invoice files, at least 2 credit notes, at least 2 with deliberate arithmetic errors,
at least 1 duplicate pair, at least 1 with altered bank details.

## 8. Cost controls (Article XII)

1. Content-hash cache checked before any billed call (FR-003).
2. Blank and separator page detection before extraction (NFR-042).
3. Classification restricted to the first page of each document.
4. Raw extraction responses retained so validation and routing changes never re-bill (FR-024).
5. Golden-set responses cached in CI keyed by sample hash and model version.
6. Cost per invoice emitted as a custom metric with a regression alert.

## 9. Deployment

Environments: `dev` (public networking, Consumption plans, scale-to-zero), `prd` (private
endpoints, Premium plan, minimum 2 replicas). Production deployment requires manual approval,
deploys as a canary at 10% traffic, watches error rate for 10 minutes, then promotes or
rolls back automatically.

## 10. Phasing

| Phase | Delivers | Demo-able outcome |
|---|---|---|
| 0 | Repo, constitution, IaC, CI/CD skeleton | `az deployment` succeeds from a clean subscription |
| 1 | Core domain: models, rules, router, matching + unit tests | full test suite green, no cloud needed |
| 2 | Extraction adapter, ingestion, orchestration, persistence | an invoice goes in and gets a routing decision |
| 3 | Review API + React UI with bounding-box overlay | **the demo** — a human resolves an exception in seconds |
| 3b | WhatsApp intake and vendor notifications | send a photo from your phone, get a reply |
| 4 | Payment instruction adapter, audit endpoint, admin console, shadow reporting | end-to-end with reporting |
| 5 | Golden set, accuracy gate, correction capture, retraining | the learning loop closes |
| 6 | Load testing, private networking, runbook, DR drill | production readiness sign-off |

Phase 3 is the one that sells. Do not defer it behind Phase 4.
