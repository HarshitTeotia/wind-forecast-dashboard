# Coding instructions

Read [README.md](README.md) for commands and [ARCHITECTURE.md](ARCHITECTURE.md) for responsibilities and domain contracts before editing. Inspect the current files and relevant tests; preserve working code and API response shapes.

## Scope and implementation

- Keep React presentation in `frontend/`, HTTP routes in `backend/app/main.py`, analytics in `analytics.py`, and ingestion in `elexon.py`.
- Fix demonstrated issues with small changes. Do not add future-feature directories, generic layers, state libraries, services, or dependencies without a current requirement.
- Reuse the central Axios instance. Preserve independent metric/day requests, cancellation, stale-result protection, loading, error, retry, and missing-data states.
- Keep configuration secrets out of source and browser-visible `VITE_` variables. Preserve ignored local `.env` files and tracked examples; do not overwrite local settings during setup.

## Data integrity

- Preserve the selection/comparison contract in ARCHITECTURE.md: publication cutoff, previous local calendar day, exact matching, error sign, conflicts, nulls, and valid-only metric samples are business rules.
- Do not change the analytics methodology during cleanup. Explain any proposed method change and its domain evidence as separate work before implementing it.
- Never create production mock electricity data, interpolate forecasts, or replace missing values with zero. Test fixtures are allowed in tests.
- Keep stored/API timestamps in UTC and displayed timestamps explicitly in Europe/London. Do not use the machine timezone for business decisions.
- Do not re-ingest unnecessarily or modify historical CSVs as part of analytics/frontend work. Ingestion is a separate manual operation; follow the README's single-process/API-stopped guidance.

## Validation and reporting

Run the backend tests and frontend production build using the commands and working directories in [README.md#validate](README.md#validate), with the existing virtual environment. Add focused regressions for behavior fixes; do not introduce a new test framework merely for a small change. Follow the README browser checklist when changing UI behavior.

Report changes, executed checks, limitations, and unresolved issues truthfully. Update the documentation that owns the affected information; link between documents rather than duplicating setup commands, architecture, or domain explanations.
