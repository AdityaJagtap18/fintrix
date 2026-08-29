# Third-Party Notices

This project incorporates the following open-source components. Each remains subject to its
own license; inclusion here does not change the proprietary license of this repository as a
whole (see `LICENSE`).

| Component | License | Used for |
|---|---|---|
| RapidFuzz | MIT | Vendor name fuzzy matching (BR-12) |
| python-dateutil | Apache-2.0 / BSD | Date parsing during normalisation |
| pytest, pytest-cov | MIT | Test runner and coverage |
| ruff | MIT | Linting and formatting |
| mypy | MIT | Static type checking |
| bandit | Apache-2.0 | Static security analysis |
| import-linter | BSD | Enforcing Article I (core has no cloud imports) |
| FastAPI | MIT | Review/admin API (Phase 3+) |
| SQLAlchemy | MIT | Data access layer (Phase 2+) |
| React, Vite | MIT | Review UI (Phase 3+) |
| PDF.js | Apache-2.0 | Document rendering in the review UI (Phase 3+) |

This list is updated as dependencies are added — see `pyproject.toml` and `src/web/package.json`.
