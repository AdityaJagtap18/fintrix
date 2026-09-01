# Invoice IQ — Accounts Payable Automation with Human-in-the-Loop

**Author & maintainer:** Aditya Jagtap · **MitraEdge** · Pune, India
**Proprietary — all rights reserved.** Not open source. See `LICENSE`.

Production-grade AP automation on **Azure AI Document Intelligence v4.0 (`2024-11-30` GA)**.
Ingests supplier invoices from email/blob/API, classifies and extracts them, validates them
against business rules, performs a 3-way match against PO and goods-receipt data, and routes
anything below confidence to a **human review queue** whose corrections become training data.

> **Outcome shown in the demo dataset:** 87% straight-through processing across 40+ vendor
> layouts, average handling time 8 min → 40 sec, field-level accuracy 91% → 96% after three
> retraining cycles.

---

## 1. Why this exists

Mid-market finance teams key invoices by hand. A clerk costs roughly $15–20 per processed
invoice once you count rework, late-payment penalties and duplicate payments. This system
removes the keying, but — critically — it does **not** pretend the model is always right.
Everything below a configurable confidence threshold lands in a review UI where a human
confirms the value against the highlighted region on the page. That is the difference between
a demo and something a CFO will sign off on.

---

## 2. Architecture

```
                ┌───────────────┐
  WhatsApp (ACS)│               │
  Email (Logic  │               │
  App)          │               │
  API (APIM) ──►│ Landing Blob  │
  Portal upload │  (raw/)       │
  SFTP watch    │               │
                └───────┬───────┘
                        │ Event Grid: BlobCreated
                        ▼
                ┌───────────────────────┐
                │ Ingest Function        │  hash → dedupe → virus scan hook
                │ (Python, isolated)     │  page count → cost pre-check
                └───────┬───────────────┘
                        │ start orchestration
                        ▼
        ┌───────────────────────────────────────────────┐
        │ Durable Functions orchestrator                 │
        │                                                │
        │  1. classify      (custom classification model)│
        │  2. split         (multi-invoice PDF → parts)  │
        │  3. extract       (prebuilt-invoice / custom)  │
        │  4. normalise     (currency, dates, units)     │
        │  5. validate      (rules engine)               │
        │  6. match         (3-way: INV ↔ PO ↔ GRN)      │
        │  7. route         (confidence router)          │
        └───────┬───────────────────────────┬───────────┘
                │ STP                        │ EXCEPTION / REVIEW
                ▼                            ▼
        ┌───────────────┐            ┌──────────────────┐
        │ Payment       │            │ Review queue      │
        │ instruction   │            │ (Cosmos DB)       │
        │ (Service Bus) │            │ + AOAI explainer  │
        └───────┬───────┘            └────────┬─────────┘
                │                              │
                ▼                     (shadow mode: every
        ┌───────────────┐              invoice comes here,
        │ Vendor notify │              decision recorded)
        │ (WhatsApp)    │
        └───────────────┘
                                              │
                                     ┌────────▼─────────┐
                                     │ Review UI (React)│
                                     │ PDF.js + bbox    │
                                     │ overlay          │
                                     └────────┬─────────┘
                                              │ corrections
                                              ▼
                                     ┌──────────────────┐
                                     │ Training feedback │
                                     │ store (Blob)      │
                                     │ → active learning │
                                     └──────────────────┘
```

### Azure services

Every service below has a one-line reason. A service with no requirement behind it does not
go in the architecture — see the "deliberately excluded" list in `specs/001-ap-automation/plan.md`.

| Concern | Service | Why |
|---|---|---|
| WhatsApp intake | Communication Services | vendors send invoice photos from their phone; status replies out |
| Email intake | Logic Apps | mailbox polling with no code to maintain |
| API intake | API Management | rate limiting and centralised auth on upload |
| Extraction | Azure AI Document Intelligence v4.0 | `prebuilt-invoice`, custom classifier, custom neural |
| PII redaction | Azure AI Language | strip names and addresses before any log write |
| Exception explanations | Azure OpenAI | plain-English reason for a block, never in the decision path |
| UI hosting | Static Web Apps | review and admin frontends, built-in auth |
| Security posture | Defender for Cloud | continuous, demonstrable to an auditor |
| Landing / archive | Blob Storage (hot → cool lifecycle) | cheap, Event Grid native |
| Eventing | Event Grid + Service Bus | at-least-once, DLQ, ordered per-vendor sessions |
| Orchestration | Durable Functions | checkpointed multi-step workflow, replayable |
| Review API | Container Apps (FastAPI) | scale-to-zero, easy private networking |
| Transactional data | Cosmos DB (serverless) | invoices, lines, PO/GRN, audit — embedded per-document, partitioned by vendor; no idle-cost floor |
| Raw payloads | Blob (`analyzed/`) | full DI JSON kept for reprocessing without re-billing |
| Secrets | Key Vault + Managed Identity | zero keys in code or CI |
| Telemetry | App Insights + OpenTelemetry | STP rate, cost/doc, confidence histogram |

---

## 3. The human-in-the-loop design

This is the heart of the product.

### Shadow mode — how automation gets switched on

Confidence routing ships **enabled but shadowed**. The router computes its decision on every
invoice; shadow mode sends every invoice to a human anyway and records what the system *would*
have done. After a month you can tell the customer, from their own data: *"of the 4,000 invoices
your team reviewed, we would have auto-approved 3,300 and been wrong on 11."*

Switching automation on then becomes their decision backed by evidence, not your promise.
`RoutingPolicy.shadow_mode` defaults to `True` — automation is always an explicit, audited choice.

**Routing decision** (`src/core/routing/confidence_router.py`):

```
overall = min(field_confidence for field in REQUIRED_FIELDS)

if hard_validation_failed        -> EXCEPTION (reason: validation)
elif duplicate_detected          -> EXCEPTION (reason: duplicate)
elif match_status == MISMATCH    -> EXCEPTION (reason: three_way_match)
elif overall >= auto_threshold   -> STRAIGHT_THROUGH
elif overall >= review_threshold -> REVIEW (field-level, only low fields editable)
else                             -> FULL_REVIEW (all fields shown)
```

While `policy.shadow_mode` is on, all of the above is recorded on `shadow_decision` and the
returned decision is always REVIEW.

Thresholds are **per-vendor**, stored in Cosmos DB, and tuned from observed accuracy. A vendor whose
layout you have seen 500 times earns a lower threshold than a new one. This is the single most
commercially persuasive feature in the whole system.

**Review UI contract.** Every extracted field carries `value`, `confidence`, `page`, and
`polygon` (from DI `boundingRegions`). The UI draws the polygon over the rendered page so the
reviewer's eye goes straight to the number. Reviewers never read the whole invoice.

**Feedback loop.** Every correction writes `{doc_hash, field, model_value, human_value,
model_version, timestamp}` to the feedback store. `scripts/build_training_set.py` turns
accumulated corrections into a labelled dataset; the retraining workflow promotes a new model
version only if the accuracy gate in `tests/golden/` passes.

---

## 3b. What this system does not do

**It never moves money.** On approval it emits a structured *payment instruction* — vendor
reference, amount, currency, due date, approval evidence — which the customer's existing
treasury or ERP system executes. No banking credentials are stored and no transfer is ever
initiated. Payment execution pulls in banking rails and maker–checker regulatory controls;
staying on the instruction side achieves the same business outcome and removes a bank
integration from the critical path to go-live.

Bank details on the instruction come from the customer's **vendor master**, never from the
invoice document. A forged remittance block on a PDF therefore cannot redirect a payment.

## 4. Validation & matching rules

Implemented in `src/core/validation/rules.py` as composable rules with severity `HARD` / `SOFT`:

- **Arithmetic** — line totals sum to subtotal; subtotal + tax = total (tolerance ₹1 / $0.01)
- **Duplicate** — `(vendor_tax_id, invoice_number)` unique; plus fuzzy near-duplicate on
  `(vendor, amount, date ±3d)` to catch re-sent scans
- **Vendor master** — resolve extracted vendor name/tax id to master record via exact tax-id
  match, then RapidFuzz name match ≥ 88 with address corroboration
- **Tax** — GSTIN checksum and state-code validation for Indian invoices; VAT-ID format per
  country; tax rate plausibility vs vendor history
- **Dates** — invoice date not in future, not older than N days, due date ≥ invoice date
- **Currency** — ISO-4217; FX rate lookup for non-base currency
- **PO match** — quantity and unit price within tolerance, PO not already fully consumed
- **Bank detail change** — if payee bank account differs from vendor master, force manual
  approval. This is fraud control and buyers ask about it in every single deal.

---

## 5. Repository layout

```
.
├── .github/workflows/       ci.yml, cd.yml, accuracy-gate.yml, infra-plan.yml, codeql.yml
├── infra/                   Bicep: main.bicep + modules/*
├── src/
│   ├── core/                pure, testable domain logic (no Azure SDK in signatures)
│   │   ├── extraction/      provider interface + Document Intelligence impl
│   │   ├── validation/      rules engine
│   │   ├── matching/        vendor resolution + 3-way match
│   │   ├── routing/         confidence router
│   │   └── persistence/     repositories
│   ├── functions/           Azure Functions: ingest trigger + durable orchestrator
│   └── api/                 FastAPI review/approve/audit endpoints
├── tests/
│   ├── unit/                fast, no network
│   ├── integration/         hits a sandbox DI resource
│   └── golden/              50 invoices + expected JSON → accuracy regression gate
├── docs/                    architecture, cost model, runbook, ADRs
└── scripts/                 seed data, build training set, cost report
```

---

## 6. CI/CD

| Workflow | Trigger | Does |
|---|---|---|
| `ci.yml` | PR | ruff, mypy, bandit, pytest unit + coverage gate 80%, build container, Trivy scan |
| `accuracy-gate.yml` | PR touching `src/core/extraction` or model config | runs golden set, fails if field-level F1 drops >1pt vs `main` baseline |
| `infra-plan.yml` | PR touching `infra/` | `az deployment what-if`, posts diff as PR comment |
| `cd.yml` | merge to `main` | OIDC login → deploy Bicep to dev → smoke tests → **manual approval env** → prod, canary revision on Container Apps, auto-rollback on 5xx |
| `codeql.yml` | schedule | static security analysis |

**No secrets in GitHub.** Auth is federated OIDC to a user-assigned managed identity; the
only repo-level values are `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`.

---

## 7. Getting started

```bash
git clone <your-fork> && cd ap-automation
cp .env.example .env
make setup            # venv + deps + pre-commit
make test             # unit tests
make infra-dev        # deploy Bicep to your dev subscription
make seed             # load sample vendors, POs, GRNs
make run-api          # FastAPI on :8000
```

See `docs/RUNBOOK.md` for operations and `docs/COST_MODEL.md` for per-1000-invoice pricing.

## 8. Author

Built and maintained by **Aditya Jagtap** (MitraEdge).

I design and deliver document-intelligence systems on Azure — extraction pipelines,
human-in-the-loop review workflows, and the MLOps around them. If you are evaluating
AP automation for your finance team, `docs/PILOT_SOW.md` describes a fixed-fee four-week
pilot run on your own invoices, in your own Azure subscription.

- GitHub: _add your handle_
- LinkedIn: _add your profile_
- Email: _add your address_

## 9. License

**Proprietary. All rights reserved.**

Copyright (c) 2026 MitraEdge (Aditya Jagtap). This software is confidential and is
licensed, not sold. No right to use, copy, modify, or distribute is granted except under
a separate written agreement with MitraEdge. See `LICENSE` for full terms and
`THIRD_PARTY_NOTICES.md` for the open-source components it incorporates.

Evaluating this for your finance team? See `docs/PILOT_SOW.md`.
