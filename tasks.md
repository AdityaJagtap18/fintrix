# Task Breakdown — 001-ap-automation

**Implements:** `spec.md` · **Follows:** `plan.md` · **Governed by:** `.specify/memory/constitution.md`
**Owner:** Aditya Jagtap, MitraEdge

**Conventions**
- `[P]` — parallelisable with other `[P]` tasks in the same phase (touches different files)
- Each task states its **Requirement**, **Files**, and **Done when** — a testable condition
- Article XI applies: write the failing test first, then the implementation
- Do not start a task whose blocking open question (spec §13) is unresolved

---

## Phase 0 — Foundation

- **T-001** Initialise repository, `.gitignore`, `LICENSE`, `pyproject.toml` with ruff/mypy/pytest config, `Makefile`, `pre-commit`.
  *Done when:* `make lint` and `make test` both run and exit cleanly on an empty suite.
- **T-002** Author `.specify/memory/constitution.md` and both spec documents into the repo.
  *Done when:* `docs/` contains constitution, spec, plan, tasks.
- **T-003** `[P]` Bicep: managed identity, storage with lifecycle tiering, containers `raw/analyzed/quarantine/feedback/training`. **Req:** NFR-031, NFR-041.
  *Done when:* `az deployment group create` succeeds and `allowSharedKeyAccess` is `false`.
- **T-004** `[P]` Bicep: Document Intelligence account with `disableLocalAuth: true`, RBAC role assignment to the identity. **Req:** Article III.
  *Done when:* a key-based call returns 401 and a token-based call returns 200.
- **T-005** `[P]` Bicep: Service Bus with queues `invoice-extract`, `invoice-review`, `erp-post`, dead-lettering enabled. **Req:** AC-11.3.
- **T-006** `[P]` Bicep: Cosmos DB with Entra-only authentication (`disableLocalAuth`), Log Analytics, App Insights.
- **T-007** `[P]` Bicep: Container Apps environment, Functions Premium plan, both with the user-assigned identity.
- **T-008** Entra app registration with federated credentials; GitHub environments `dev` and `production` with required reviewers on `production`. **Req:** Article III.
  *Done when:* a workflow authenticates to Azure with no stored secret.
- **T-009** `[P]` `ci.yml`: ruff, mypy strict, bandit, pytest with 80% coverage gate, Bicep lint, container build, Trivy scan.
- **T-010** `[P]` `cd.yml`: build/push image, deploy dev, smoke test, gated prod deploy with 10% canary and automatic rollback. **Req:** plan §9.
- **T-011** `[P]` `infra-plan.yml` posting an `az deployment what-if` diff as a PR comment. **Req:** Article IX.
- **T-012** `[P]` Import-linter rule enforcing that `src/core` imports nothing internal and no SDK. **Req:** Article I.
  *Done when:* adding `import azure.storage.blob` to a core module fails CI.

---

## Phase 1 — Core domain (no cloud, no credentials)

- **T-020** Domain models: `Invoice`, `ExtractedField`, `BoundingRegion`, `LineItem`, `ValidationIssue`, `MatchResult`, `RoutingResult`, and the `RoutingDecision`/`Severity`/`MatchStatus` enums. **Req:** FR-022, FR-023.
  *Done when:* `Invoice.confidence(field)` returns 1.0 for a field whose source is `human`.
- **T-021** `[P]` Normalisation: dates to ISO 8601, amounts to two-place `Decimal`, currency to ISO 4217, whitespace and OCR artefact cleanup. **Req:** FR-025.
  *Done when:* `₹1,250.00`, `1250`, and `Rs 1250.00` all normalise identically.
- **T-022** Rule registry and `ValidationContext` with `VendorLookup` and `DuplicateLookup` protocols. **Req:** FR-034.
  *Done when:* a new rule is registered by decorator with no change to `validate()`.
- **T-023** `[P]` Rules BR-01, BR-02, BR-03 (required fields, line sum, total arithmetic) with value suggestions. **Req:** FR-030, FR-033.
- **T-024** `[P]` Rules BR-04, BR-05 (exact and near duplicate). **Req:** AC-04.1, AC-04.2.
- **T-025** `[P]` Rules BR-06, BR-07, BR-08 (date sanity).
- **T-026** `[P]` Rule BR-09 — tax identifier format and checksum per jurisdiction. **Blocked by Q-04.**
  *Done when:* a GSTIN with one transposed digit fails checksum and produces a HARD issue.
- **T-027** `[P]` Rules BR-10, BR-14, BR-15, BR-16 (currency, tax plausibility, ceiling, sign).
- **T-028** `[P]` Rules BR-11, BR-12 — vendor resolution by tax ID then fuzzy name. **Req:** FR-030.
- **T-029** `[P]` Rule BR-13 — bank detail change control. **Req:** AC-05.1.
  *Done when:* a mismatched payee account produces HARD regardless of confidence.
- **T-030** Three-way match: PO resolution, price tolerance, over-invoicing detection, closed-PO rejection, non-PO vendors. **Req:** FR-040..044, AC-06.1..06.5.
- **T-031** `RoutingPolicy` and `route()` implementing plan §5 in full, including the
  `shadow_mode` flag that records the computed decision and returns REVIEW. **Req:** FR-050..057.
  *Done when:* with shadow mode on, a perfect invoice returns REVIEW while
  `shadow_decision` reads STRAIGHT_THROUGH.
  *Done when:* all of AC-01.1, AC-01.3, AC-04.1, AC-05.1, AC-06.2 pass as tests.
- **T-032** Repository protocols in `core/persistence` — interfaces only. **Req:** Article I.
- **T-033** Unit test suite for Phase 1. **Req:** Article XI.
  *Done when:* coverage on `src/core` is ≥90% and the suite runs offline in under 10 seconds.

---

## Phase 2 — Extraction and pipeline

- **T-040** `DocumentExtractor` ABC, `ExtractionRequest`, and the transient/permanent error split. **Req:** NFR-050, Article II.
- **T-041** Document Intelligence adapter: analyse, field mapping to canonical names, confidence, bounding regions, line items with row confidence, typed value coercion. **Req:** FR-020..023.
  *Done when:* a sample invoice yields all 15 canonical fields with page and polygon populated.
- **T-042** `[P]` Classification adapter and quarantine of non-invoices. **Req:** FR-010, AC-11.1.
- **T-043** `[P]` Multi-invoice detection and splitting with page-range provenance. **Req:** FR-011, AC-07.1.
- **T-044** `[P]` Content-hash cache checked before any billed call. **Req:** FR-003, Article XII.
  *Done when:* re-submitting an identical file records zero extraction charges.
- **T-045** `[P]` Blank and separator page filter before extraction. **Req:** NFR-042.
- **T-046** Cosmos DB containers, partition keys, unique key policies, and RBAC role definitions for every entity in plan §3, including the unique key policy on `invoices` (`/invoice_number`, scoped to the `vendor_id` partition) and the create/read-only `audit-log-writer` role on `audit_log`. **Req:** FR-090, AC-10.2.
  *Done when:* a replace or delete against `audit_log` as the application identity is rejected by Cosmos RBAC.
- **T-047** `[P]` Cosmos DB repository implementations of the Phase 1 protocols.
- **T-048** `[P]` Blob and Service Bus adapters using Managed Identity only.
- **T-049** Ingestion triggers: Event Grid blob trigger and authenticated HTTP upload; hash, dedupe, page count, quarantine unopenable files. **Req:** FR-001..005.
- **T-050** Mailbox ingestion via a Logic App writing attachments to `raw/`. **Req:** FR-001.
- **T-050a** ACS Advanced Messaging provisioning: Meta Business account, WhatsApp Business
  number, message templates for acknowledgement and final status. **Blocked by Q-08.**
  *Note:* number approval takes days — start this at the beginning of Phase 2, not the end.
- **T-050b** WhatsApp inbound webhook: signature verification, media download to `raw/`, sender
  number recorded, unsupported-content reply. **Req:** FR-001, AC-12.1, AC-12.4.
- **T-050c** Sender-to-vendor resolution by phone number, flagging unrecognised senders without
  rejecting. **Req:** FR-001c, AC-12.5.
- **T-050d** `[P]` Outbound notification service: acknowledgement on accept, status on terminal
  state, queued and retried, delivery failure never blocks processing. **Req:** FR-001a.
  *Done when:* a message containing an amount or bank detail fails a unit test (FR-001b).
- **T-050e** `[P]` API Management in front of the upload endpoint with rate limiting and Entra
  validation. **Req:** FR-001, NFR-021.
- **T-050f** `[P]` PII detection and redaction via Azure AI Language before any log write. **Req:** NFR-025.
  *Done when:* a name or address present in a document never appears in App Insights.
- **T-051** Durable orchestrator implementing plan §4, with sub-orchestration fan-out for splits and the 48-hour escalation timer. **Req:** FR-011, AC-11.4.
  *Done when:* the orchestrator function contains no IO and replays deterministically.
- **T-052** Activity functions — one thin module per pipeline stage, each idempotent on `document_id`. **Req:** Article V.
  *Done when:* replaying any activity twice leaves identical state and no duplicate ERP post.
- **T-053** Dead-letter handling and a replay script with a dry-run mode. **Req:** AC-11.3.
- **T-054** Integration tests for the pipeline against sandbox resources.

---

## Phase 3 — Human review (the demo)

- **T-060** Review API: queue listing with filters, claim with exclusive optimistic lock, release, submit with corrections. **Req:** FR-060..065.
  *Done when:* AC-03.1 and AC-03.3 pass — a second claim returns conflict, a lockless submit is rejected.
- **T-061** `[P]` Lock expiry sweeper returning abandoned invoices to the queue. **Req:** FR-062, AC-03.2.
- **T-062** `[P]` Entra ID authentication and app-role authorisation for Reviewer, Approver, Administrator, Auditor. **Req:** NFR-021, NFR-022.
- **T-063** `[P]` Short-lived user-delegated document URLs. **Req:** NFR-024.
- **T-064** `[P]` Approver-role enforcement on bank-detail-change approvals. **Req:** FR-066, AC-05.3.
- **T-065** React shell: routing, auth, queue view with filters and sorting. **Req:** FR-060.
- **T-066** Document viewer: PDF.js render with a bounding-box overlay layer. **Req:** FR-063, AC-02.2.
  *Done when:* selecting a field scrolls to its page and highlights its polygon.
- **T-067** Field editor: only flagged fields editable, others read-only and visible, rule suggestions offered as one-click accept. **Req:** FR-064, AC-02.1, AC-02.3.
- **T-068** `[P]` Issue panel showing every rule outcome in plain language, with the vendor-master comparison for bank-detail changes. **Req:** AC-05.2.
- **T-069** `[P]` Duplicate panel linking the prior matching document. **Req:** AC-04.2.
- **T-070** `[P]` Approve/reject flow with mandatory rejection reason and override capture. **Req:** FR-065, AC-04.3.
- **T-071** Submission raises the Durable external event and records corrections. **Req:** FR-070, AC-02.4.
- **T-072** `[P]` Keyboard-first interaction — tab between review fields, enter to accept a suggestion, submit without touching the mouse.
  *Done when:* a reviewer completes a two-field correction in under 15 seconds unassisted.
- **T-073a** `[P]` Exception explainer over Azure OpenAI: structured-input-only prompt, output
  validated so every number traces to an input field, graceful degradation to raw rule outcomes.
  **Req:** FR-068, FR-069, AC-14.1..14.4.
  *Done when:* disabling the endpoint still allows a reviewer to complete a review.
- **T-073b** `[P]` Shadow agreement capture on submission — agreement flag and changed-field
  list per invoice. **Req:** FR-058, AC-13.3.
- **T-073** E2E test: WhatsApp send → route to review → claim → correct → approve → hand off →
  vendor notified.

---

## Phase 4 — ERP, audit, reporting

- **T-080** `PaymentInstructionAdapter` ABC plus a mock adapter writing a structured instruction
  to blob. Instruction references vendor bank details from the vendor master, never from the
  invoice. **Req:** FR-080, FR-083, FR-085, NFR-051.
  *Done when:* no code path can initiate a transfer and no banking credential is stored (FR-084).
- **T-081** Idempotent hand-off keyed on `invoice_id`, with backoff, failure state, and alert on
  exhaustion. **Req:** FR-081, FR-082.
  *Done when:* the same invoice handed off twice produces exactly one instruction.
- **T-082** `[P]` Audit log writer covering every state transition, human action, threshold change, and model promotion. **Req:** FR-090, Article VIII.
- **T-083** `[P]` Audit endpoint returning the complete invoice history including active model version. **Req:** FR-091, AC-10.1, AC-10.3.
- **T-084** `[P]` Metrics endpoints: STP rate, volume, average handling time, exceptions by reason and vendor, cost per invoice. **Req:** FR-092, AC-09.1..09.3.
- **T-085** `[P]` OpenTelemetry instrumentation emitting `invoice.routing.decision`, `invoice.confidence`, `invoice.cost`, `review.handling_time` as custom metrics. **Req:** Article X.
- **T-086** `[P]` Alert rules: STP rate below 60%, dead-letter depth above zero, extraction throttling, budget at 80% of forecast. **Req:** NFR-043.
- **T-087** Dashboard UI consuming the metrics endpoints. **Req:** AC-09.1.
- **T-087a** `[P]` Admin console — overview: processed, accepted, rejected, in progress, plus
  confidence distribution overall and per field. **Req:** FR-097, AC-15.1, AC-15.2.
- **T-087b** `[P]` Admin console — shadow report: would-have-auto-approved count and
  would-have-been-wrong count for a period. **Req:** FR-059, AC-13.4.
  *Done when:* the screen produces the go-live sentence from real data.
- **T-087c** `[P]` Admin console — channel health and dead-letter depth per ingestion channel. **Req:** FR-099, AC-15.5.
- **T-087d** `[P]` Admin console — model registry view with evaluation scores and promotion. **Req:** FR-098, AC-15.4.
- **T-087e** `[P]` Administrator role enforced on every console endpoint. **Req:** FR-095, AC-15.6.
- **T-088** `[P]` Vendor policy administration — thresholds, ceiling, PO requirement, and shadow
  mode toggle, each audited. **Req:** FR-051, FR-057, FR-096, AC-13.5, AC-15.3.
- **T-089** `[P]` Data retention and automated purge job. **Req:** NFR-031, FR-093.

---

## Phase 5 — Accuracy and the learning loop

- **T-090** Assemble the golden set to the composition required in plan §7. **Blocked by A-05.**
  *Done when:* 50+ documents with expected JSON exist, covering all listed edge cases.
- **T-091** Evaluation harness computing per-field precision, recall, F1, and document exact-match, with meaning-based comparison. **Req:** Article IV.
- **T-092** Baseline comparison script failing on a macro-F1 drop above 0.01, emitting a markdown table. **Req:** Article IV.
- **T-093** `accuracy-gate.yml` running on extraction-related PRs, caching responses by sample hash, posting results as a PR comment. **Req:** Article IV, Article XII.
  *Done when:* a deliberately broken field mapping fails the build.
- **T-094** `[P]` Correction persistence with full provenance. **Req:** FR-070, AC-08.1.
- **T-095** `[P]` Training-set builder converting corrections into labelled data. **Req:** FR-071, AC-08.2.
- **T-096** Model training script and model registry with prod/candidate aliases. **Req:** FR-072, FR-074.
- **T-097** `retrain.yml`: build dataset, train candidate, evaluate, open a promotion PR only on improvement. **Req:** AC-08.3, AC-08.4.
  *Done when:* a candidate that does not beat the incumbent produces no promotion PR.
- **T-098** `[P]` Active-learning queue prioritising low-confidence documents for labelling.
- **T-099** `[P]` Drift detection alerting on a shift in the live confidence distribution.

---

## Phase 6 — Production readiness

- **T-100** Load test sustaining 500 invoices/hour with no queue growth. **Req:** NFR-002.
- **T-101** `[P]` Latency verification against NFR-001, NFR-003, NFR-004.
- **T-102** `[P]` Private endpoints and public network access disabled in production. **Req:** NFR-020.
- **T-103** `[P]` gitleaks in CI; verify no personal data reaches application logs. **Req:** NFR-025.
- **T-104** `[P]` Extraction-outage resilience test: block the service for four hours, confirm queueing and clean drain. **Req:** NFR-012.
- **T-105** Disaster recovery drill — rebuild an environment from `infra/` alone and restore data. **Req:** NFR-013.
  *Done when:* a full rebuild completes within the 4-hour RTO with no manual portal steps.
- **T-106** `[P]` Runbook covering every alert defined in T-086.
- **T-107** `[P]` Cost report verifying the per-invoice target and the effectiveness of each control in plan §8.
- **T-108** `[P]` Demo recording and README with architecture diagram and a linked walkthrough video.
- **T-109** Penetration test of the API surface, including the WhatsApp webhook, and remediation.
- **T-110** `[P]` Defender for Cloud enabled with a documented secure-score baseline. **Req:** NFR-020, NFR-023.
- **T-111** Shadow-mode go-live review: run for the agreed period, produce the agreement report,
  and obtain written customer sign-off before disabling shadow mode for any vendor. **Blocked by Q-06.**
  *Done when:* automation is switched on from the customer's own measured data, not a promise.

---

## Dependency summary

```
Phase 0 ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 6
                            │           │           │
                            └───────────┴──► Phase 5 ┘
```

Phase 1 requires no cloud resources and can begin immediately. Phase 5 needs Phase 2 for
extraction and Phase 3 for corrections, but T-090 through T-093 (golden set and accuracy gate)
should be built as soon as Phase 2 lands — the gate is worth far less added retrospectively.

**Two long-lead items to start early.** T-050a (WhatsApp Business number approval) takes days of
external waiting and blocks all of Phase 3b — raise it at the start of Phase 2. T-111 (shadow-mode
go-live review) needs weeks of real traffic before it can conclude, so the shadow reporting in
T-087b must exist well before the customer expects an automation decision.
