# Engineering Constitution — FINTRIX

**Version:** 1.0.0 · **Ratified:** 2026-08-28 · **Maintainer:** Aditya Jagtap, MitraEdge

These are non-negotiable. Any generated code, plan, or task that violates an article must be
rejected and regenerated. When a requirement in a spec conflicts with an article here, the
article wins and the spec must be amended.

---

## Article I — The core is pure

Business logic in `src/core/` MUST NOT import any cloud SDK, HTTP client, database driver, or
framework. It operates on domain dataclasses only. IO happens at the edges: functions,
API routers, repositories. This is also what makes the project FOSS-honest: every engine
`core/` talks to through an adapter (today: Tesseract for OCR) is swapped by writing one new
class against `DocumentExtractor`, never by touching `core/` itself.

**Test:** `grep -r "azure\.\|import requests\|sqlalchemy" src/core/models.py src/core/validation/ src/core/routing/ src/core/matching/` returns nothing.

**Why:** the rules engine and router must be unit-testable with no credentials, no network,
and no cost. A finance analyst must be able to read `rules.py` and confirm the business logic.

## Article II — One extraction interface

All document extraction goes through `DocumentExtractor` (`src/core/extraction/base.py`).
Engine-specific field names are mapped to canonical names at the adapter boundary and never
leak downstream. Adding a new engine MUST require changes to exactly one new class.

## Article III — No secrets, anywhere

No API keys, connection strings, passwords, or tokens in source, config files, CI variables,
or logs. Local development reads credentials from an untracked `.env` file (`.gitignore`
excludes it; `.env.example` documents the shape with placeholder values only). Production
reads them from environment variables injected at deploy time (e.g. a `.env.prod` that never
leaves the VPS and is never baked into a Docker image layer). CI authenticates to the VPS
with a dedicated SSH deploy key stored as an encrypted GitHub Actions secret, scoped to
nothing but that deploy. The only repository secret GitHub itself provisions, `GITHUB_TOKEN`,
needs no entry in the repo's own secret list.

**Test:** gitleaks scan passes; `git ls-files` shows no `.env`, `.env.prod`, private key, or
other credential file tracked by the repository.

## Article IV — Accuracy is a build gate, not a hope

Any change touching extraction, normalisation, or validation MUST run the golden-set
evaluation. A macro-F1 drop greater than 0.01 versus the stored baseline fails the build.
Baselines change only through a reviewed PR that explains why.

## Article V — Every write is idempotent

Every activity, message handler, and API mutation is keyed on `document_id` or an explicit
idempotency key. Replaying any message or re-running any orchestration produces the same
state and never double-posts to the ERP.

## Article VI — Nothing is silently discarded

A document that cannot be processed goes to `quarantine/` with a recorded reason. A message
that exhausts retries goes to a dead-letter queue and raises an alert. There is no code path
that swallows an exception and returns success.

## Article VII — Confidence is never rounded up

The system never presents a low-confidence value as fact. Any field below the configured
threshold is routed to a human with its confidence score and its location on the page
displayed. The system may say "I am not sure"; it may not guess.

## Article VIII — Everything a human touches is audited

Every correction, approval, rejection, threshold change, and model promotion writes an
append-only audit record with actor, timestamp, before value, and after value. Audit records
are never updated or deleted.

## Article IX — Infrastructure is code

No resource is hand-configured through a web console or an imperative one-off command that
isn't also captured in version control. Every environment is reproducible from the
repository's Dockerfiles, `docker-compose.prod.yml`, nginx vhost config, and GitHub Actions
deploy workflow alone — no undocumented state lives only on the VPS. A pull request that
changes any of these MUST describe what changes on the next deploy before merge.

## Article X — Observability is business-first

Telemetry answers business questions before technical ones. Straight-through-processing
rate, cost per invoice, and exception reasons are first-class metrics with alerts. CPU and
memory are secondary.

## Article XI — Tests precede implementation

For every functional requirement, the acceptance test is written and failing before the
implementation is written. Coverage on `src/core/` MUST NOT fall below 90%; overall
MUST NOT fall below 80%.

## Article XII — Cost is a design constraint

Every code path that calls a billed API MUST be justified. Content-hash caching, blank-page
filtering, and duplicate detection run before extraction, not after. Cost per invoice is a
tracked metric with a regression alert.
