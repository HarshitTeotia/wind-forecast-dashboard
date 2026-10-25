# Wind Forecast Dashboard

**Wind Intelligence** is a local React + FastAPI dashboard for historical UK day-ahead wind forecast analysis in January 2026. It ingests Elexon data, selects forecasts available at a configurable previous-day cutoff, and displays forecast accuracy and coverage.

Features include monthly KPIs, daily forecast/actual and signed-error charts, publication/cutoff tooltips in UK time, and explicit missing-data states. The default delivery date is January 15; the default cutoff is 10:00 Europe/London. That cutoff is an illustrative assumption, not an official market deadline.

See [ARCHITECTURE.md](ARCHITECTURE.md) for responsibilities, data flow, methodology, and tradeoffs. [AGENTS.md](AGENTS.md) contains coding instructions.

## Docker (optional)

Docker Engine 25+ / Docker Desktop with Compose 2.20.2+ and Linux containers can run the bundled demonstration without local Node.js or Python. From the project root:

```sh
docker compose config
docker compose up --build -d
```

Open **http://localhost:8080**. Nginx serves the production UI and forwards `/api/` to FastAPI on the default Compose network. Backend port 8000 is not published to the host. Both runtime containers use non-root users. No local `.env`, virtual environment, node_modules, tests, or test artifacts are copied into runtime images.

```sh
docker compose logs -f
docker compose down
```

The two January CSVs are bundled in the backend image with read-only permissions; ingestion is not part of container startup. To update them, stop the stack, run ingestion using the local backend workflow below, review/validate the new data, and rebuild with `docker compose up --build -d`. There are no data volumes or additional services. Commit both required snapshot CSVs with these files when publishing the repository so a fresh clone can build in one command. They total about 902 KB and are explicitly allowed by `.gitignore`; the backend Dockerfile copies them at build time, so omitting them makes a fresh-clone Docker build fail. Keep only these two snapshots versioned, retain their Elexon provenance, and review provider redistribution terms before making the data public.

The frontend Docker build sets `VITE_API_BASE_URL=/`, because Axios already requests paths beginning with `/api/`. Browser requests therefore use the same origin; `backend` is only a container-network hostname. Local Vite configuration and `.env` fallbacks remain unchanged, and Docker is optional.

After startup, verify these URLs through Nginx, then test chart loading, date/cutoff changes, and missing hours at cutoff 00:00:

- `http://localhost:8080/api/health`
- `http://localhost:8080/api/metrics?cutoff_hour=10`
- `http://localhost:8080/api/comparison?date=2026-01-15&cutoff_hour=10`

Docker Compose configuration, image builds, startup, Nginx forwarding, compression/cache headers, and browser loading through port 8080 were verified during the performance review. Fingerprinted JS/CSS assets are cached for one year; HTML revalidates so it can discover new asset filenames. See the measured results in [ARCHITECTURE.md](ARCHITECTURE.md#performance-review).

## Prerequisites

For local development without Docker:

- Node.js 22.12+ (22.x) or 24+ and npm. Node 24 was used for validation.
- Python 3.12+ with pip and venv. Python 3.12 was used for validation.
- Network access to Elexon for ingestion; the dashboard itself reads local CSVs.

Stack: React/JSX, Vite, Tailwind CSS, Recharts, Axios; FastAPI, Uvicorn, pandas, httpx, and python-dotenv. Install frontend dependencies from `frontend/`, not the project root.

## Install

Start in the project root. Create local environment files only if absent; preserve existing settings.

Windows PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
cd ..\frontend
npm ci
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

If `python` is unavailable or opens the Microsoft Store, use the full path to an installed Python interpreter to create the environment. If activation is blocked, skip activation and use `.\.venv\Scripts\python.exe` instead of `python` while in `backend/`; no execution-policy change is required.

macOS/Linux:

```sh
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
cd ../frontend
npm ci
[ -f .env ] || cp .env.example .env
```

| Local file | Variable | Development fallback |
| --- | --- | --- |
| `frontend/.env` | `VITE_API_BASE_URL` | `http://localhost:8000` |
| `backend/.env` | `FRONTEND_ORIGIN` | `http://localhost:5173` |

The backend explicitly loads `backend/.env`; process variables take precedence. Vite loads frontend environment configuration. Restart servers after changing configuration; production frontend values require a rebuild. `VITE_` values are public browser configuration, never secrets. Local `.env` files are ignored by Git; examples are retained. CORS allows the configured exact origin (scheme, hostname, port); it is not authentication. This project is intended for local use.

## Obtain the historical CSVs

Skip this step when both snapshot files already exist. Only the two required CSVs are eligible for version control; other generated data remains ignored. If they are missing from your checkout, run ingestion before building the backend image or starting analytics. Run **one ingestion process with the API stopped**; the two CSVs are replaced individually, not as a transaction.

From `backend/`, Windows:

```powershell
.\.venv\Scripts\python.exe -m app.elexon
```

From `backend/`, macOS/Linux with the environment activated:

```sh
python -m app.elexon
```

Successful ingestion writes:

| File | Columns |
| --- | --- |
| `backend/data/wind_actuals.csv` | `startTime,generation` |
| `backend/data/wind_forecasts.csv` | `startTime,publishTime,generation` |

The sources are Elexon FUELHH (`fuelType=WIND`) and historical WINDFOR revisions. Requests cover January 1 through February 1, 2026; forecast publications also cover December 25-31, 2025, with an earlier-week coverage check. See [ingestion details](ARCHITECTURE.md#ingestion) for boundary handling and bounded lookback extension. Output reports exclusions, duplicates, invalid values, and missing intervals. Runs replace CSVs instead of appending; no values are fabricated. Re-ingestion may reflect upstream revisions, so retain the existing files when reproducing a previous analysis.

The recorded snapshot contains 1,488 half-hourly actuals and 18,080 forecast revisions over 744 hourly delivery timestamps. These describe the existing snapshot, not guaranteed counts for every future download.

## Run

Use two terminals. From `backend/`, Windows:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host localhost --port 8000
```

On macOS/Linux, activate the backend environment and run:

```sh
python -m uvicorn app.main:app --reload --host localhost --port 8000
```

From `frontend/`, in the other terminal:

```sh
npm run dev
```

Open **http://localhost:5173**. Vite fails if port 5173 is occupied, preserving the configured CORS origin. API documentation is at **http://localhost:8000/docs**.

| API | Behavior |
| --- | --- |
| `GET /api/health` | HTTP 200 with `status: ok`; does not read CSVs |
| `GET /api/comparison?date=2026-01-15&cutoff_hour=10` | One January delivery day; omit `date` for all January hours |
| `GET /api/metrics?cutoff_hour=10` | Monthly metrics and sample/coverage counts |

Both analytics routes accept integer cutoff hours 0-23. Invalid parameters or dates outside January return HTTP 422. Missing or malformed local data returns HTTP 503. Comparison responses contain metadata and a `records` array, not a bare array. Dates in API responses are UTC; charts display Europe/London time.

Historical CSVs load lazily once per backend process. Monthly comparisons are cached for each requested cutoff (at most 24); date requests select a day from that cached month. After changing local CSVs, restart the backend to refresh the snapshot. In Docker, rebuild the backend image as described above. No ingestion or expensive analytics runs at startup.

Changing delivery date refreshes only comparison data. Changing cutoff refreshes both datasets. The dashboard cancels superseded requests and offers retry actions for failures.

## Validate

From `backend/`, Windows:

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_analytics test_elexon
```

With the backend environment activated on macOS/Linux, use `python -m unittest -v test_analytics test_elexon`.

From `frontend/`:

```sh
npm run build
```

Backend tests use temporary files and test fixtures; they do not download or replace production CSVs.

From `backend/`, run the reproducible read-only numerical and baseline diagnostics:

```powershell
.\.venv\Scripts\python.exe validate_data.py
```

On macOS/Linux with the environment activated, use `python validate_data.py`. It prints distribution statistics, every daily mean error, lead-time diagnostics, baseline differences and CSV hashes. It exits nonzero for invalid source data or baseline differences; this does not certify dataset comparability. See [Data Comparability](ARCHITECTURE.md#data-comparability) for official evidence and numerical interpretation.

From `frontend/`, run the five browser smoke tests:

```sh
npm run test:e2e
```

Playwright starts/stops FastAPI and Vite automatically unless a server is already running on the configured local port. It uses the existing backend `.venv`, existing CSVs, and installed Google Chrome by default; no re-ingestion occurs. Existing reused servers remain yours to stop. The suite tests real API values and filters, preserved gaps, UK formatting with a non-UK browser timezone, rapid changes, failure/retry, responsive widths, and empty/null test-only responses. Those fixtures never modify source CSVs or fabricate production generation.

If Google Chrome is unavailable, install Playwright Chromium with `npx playwright install chromium`, then set `PLAYWRIGHT_BROWSER_CHANNEL=chromium` before running the suite. In PowerShell use `$env:PLAYWRIGHT_BROWSER_CHANNEL = 'chromium'`; in macOS/Linux prefix the command with `PLAYWRIGHT_BROWSER_CHANNEL=chromium`. The development-only Playwright dependency does not enter the application bundle. Generated results/traces are ignored by Git. A smoke suite and successful build do not establish exhaustive browser or accessibility coverage.

The installed Starlette test client currently emits an httpx deprecation warning; tests still run. Review compatibility in a separate dependency update rather than adding a second HTTP client solely to hide the warning.

## Interpretation and limitations

This is an **hourly timestamp-matched proxy comparison**: it uses the FUELHH half-hour observation beginning at the WINDFOR target timestamp. It is not an hourly average, full half-hour forecast evaluation, or calculation of purchasing requirements. Missing/conflicting values are excluded from metrics, never treated as zero.

Dataset asset scope and forecast target-time meaning have not been fully reconciled. A shared timestamp alone does not prove that the measurements are equivalent; reported bias can reflect these differences as well as forecast error. See the [comparability review](ARCHITECTURE.md#comparability-review) before using the results for procurement decisions or proposing a new comparison method.

The existing 10:00 snapshot has 744 valid hourly comparisons with no missing forecasts or actuals. A 00:00 cutoff leaves 62 missing forecasts (22:00 and 23:00 each day). Forecasts at half past the hour are not supplied or interpolated. Dates are fixed to January 2026; this is historical analysis, not live data.
