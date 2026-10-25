# Architecture

Operational setup and commands live in [README.md](README.md). This document describes the implementation and the assumptions that reviewers must preserve.

## Runtime and responsibilities

```mermaid
flowchart LR
    E[Elexon FUELHH / WINDFOR] --> I[Manual CLI: app.elexon]
    I --> D[(Two local UTC CSVs)]
    D --> A[app.analytics]
    A --> F[FastAPI routes in app.main]
    F --> C[Central Axios client]
    C --> R[React state and Recharts]
```

Ingestion is a separate command, not an API startup task or request-triggered download. Serving the dashboard does not contact Elexon. There is no database, scheduler, or authentication. A bounded process-local cache reuses the immutable historical snapshot and requested monthly comparisons.

Optional Compose packaging uses exactly two services: `frontend` builds React in Node 24 Alpine and serves only the production output from Nginx Alpine; `backend` runs Uvicorn from Python 3.12 slim. Nginx forwards `/api/` to `backend:8000` without replacing the URI, preserving the prefix and query parameters; other paths support the React index fallback. Only frontend port 8080 is published. The browser uses relative API paths, so production communication is same-origin. Both runtimes use non-root users. Nginx keeps its PID and temporary files under `/tmp`; it uses port 80 with Docker's standard unprivileged-port support.

Compose waits for a backend HTTP health check using Python's standard library before starting the frontend. During startup it probes every second; once healthy it uses the regular ten-second interval. Nginx compresses JS/CSS/JSON, gives fingerprinted assets a one-year immutable cache lifetime, and makes HTML revalidate so new deployments can be discovered. This checks process readiness, not dataset comparability. The two snapshot CSVs are copied into the image under the existing module-relative data path, made read-only, and never regenerated on startup. Builds use service-specific contexts and allowlists in each `.dockerignore`; no root context/ignore file, volumes, or extra services are needed. Operational commands and verification limits are in the README Docker section.


Optional Vercel Services packaging uses the same FastAPI app and a static Vite service without Nginx or Compose. The root route table sends `/api/*` to `backend` unchanged and the catch-all to `frontend`. Browser requests use the public same-origin API in Vite mode `vercel`; there are no server-to-server requests and therefore no internal service bindings. Binding URLs belong only in runtime server functions, never in the static browser bundle. Both services are public through their specified rewrites; there are no internal-only services. API documentation routes remain outside the public backend prefix.

The Vercel function bundles the two historical CSVs as read-only runtime input. No filesystem persistence or cross-instance cache coherence is assumed: each function instance populates its own cache lazily, and data updates require redeployment. The existing local Docker hostnames and development CORS origin belong only to those workflows and are not Vercel service-discovery configuration. Operational setup and confirmation requirements are in the README Vercel section.

| Location | Responsibility |
| --- | --- |
| `backend/app/elexon.py` | Requests, bounded retries/range splitting, normalization, coverage reports, CSV replacement; owns the January window/data path constants |
| `backend/app/analytics.py` | CSV loading, local-day cutoffs, shared revision selection, exact matching, metrics, UTC JSON serialization |
| `backend/app/main.py` | Three HTTP routes, query validation, CORS/env loading, mapping local data failures to HTTP 503 |
| `backend/test_elexon.py` | Ingestion validation and request-failure regressions using fixtures/transports |
| `backend/test_analytics.py` | Cutoff boundaries, conflicts, missingness, matching, known metrics, timezone behavior, API validation |
| `frontend/src/services/api.js` | Reusable Axios instance, public API base URL, 10-second timeout |
| `frontend/src/App.jsx` | Selected filters, independent request lifecycles, response-envelope checks, dashboard sections and request states |
| `frontend/src/components/MetricCard.jsx` | Metric presentation and loading placeholder |
| `frontend/src/components/ForecastChart.jsx` | Recharts line/error views, axes, legend, and detailed tooltips |
| `frontend/src/format.js` | Finite-number, unit, fraction, and UK display formatting |
| `frontend/src/main.jsx`, `index.css` | React mounting/StrictMode and responsive visual styles |

The small import of period/path constants from ingestion into analytics is intentional at this size. There is no duplicated forecast-selection implementation. Ingestion and analytics validate data at different boundaries; a generic validation framework is unnecessary.

## Ingestion

Requests use `https://data.elexon.co.uk/bmrs/api/v1/datasets/{dataset}/stream` with the supplied `Accept: text/plain` header. The observed payload is a JSON array with `application/json` content type; the implementation parses the payload independently of that request header, accepts an optional `data` envelope, and rejects malformed structures. Empty ranges are reported.

- FUELHH requests settlement dates January 1 through February 1 with `fuelType=WIND`. The observed settlement end date is inclusive. Normalization independently filters WIND and keeps only deliveries in `[2026-01-01T00:00:00Z, 2026-02-01T00:00:00Z)`.
- WINDFOR first requests publications January 1 through February 1. It also requests December 25 through January 1, preserving relevant December publications for early January deliveries. Earlier weeks are checked, extending the lookback only when more January deliveries are found, with at most four checks. An empty check or bounded limit cannot prove full historical coverage and is reported.
- Non-numeric/non-finite generation, ambiguous timezone-free timestamps, missing timestamps, and off-grid deliveries are reported and excluded. Exact output duplicates are removed; conflicting values are preserved for downstream detection. Earlier revisions are never replaced by newer revisions.
- There are at most three attempts per request for supported transient errors/rate limits, with delays capped at 30 seconds. Persistent transport/range failures may split requests until the range is one day. Attempts are bounded per request; a full split download can take substantially longer than a single timeout.
- Both datasets are normalized and checked for emptiness before saving. Each CSV is written to a temporary file and atomically replaced. The pair is **not** an atomic snapshot: the second replacement can fail after the first succeeds. Run one ingestion process with the API stopped and verify both outputs before restarting. No new storage transaction layer is introduced.

These are reduced normalized CSVs; the raw response and original retrieval/receipt timestamps are not retained. Upstream corrections can change a later download, even though rerunning against unchanged responses is idempotent.

## Selection and comparison contract

1. Construct the expected January hourly grid (744 timestamps), retaining missing hours in output.
2. Convert each delivery to Europe/London to find its local calendar date.
3. Construct the selected cutoff hour on the previous local calendar day and convert it to UTC. Do not subtract a fixed 24 hours in UTC as a substitute for local calendar arithmetic.
4. Keep only revisions with `publishTime <= decisionCutoff` and take the latest eligible publication timestamp.
5. Collapse identical values at that timestamp. If values conflict or are invalid, expose the status and null value; do not choose arbitrarily or silently fall back to an older revision.
6. Look up the FUELHH value at the exact delivery timestamp. Half-hour values at `:30` are not aggregated, shifted, or used to fill forecasts.
7. Compute `errorMW = forecastMW - actualMW` and its absolute value only when both values are usable.

Positive error is overforecasting; negative is underforecasting. Original CSVs are never modified by analytics. The January window avoids daylight-saving transitions; extending to other months requires an explicit policy/test for ambiguous or nonexistent cutoff hours (for example, 01:00 on a clock-change day).

`publishTime` is treated as the information-availability boundary. This prevents look-ahead relative to stored publication times, but there is no receipt-time archive proving the instant a trader could retrieve each publication.

## Metrics and API boundary

Both analytics endpoints call `load_comparison`; the comparison endpoint may filter to one date, while metrics always use the full January grid. The first analytics request loads both CSVs lazily into a process-local snapshot. A bounded `lru_cache(maxsize=24)` retains a monthly comparison for each requested cutoff. A lock around lookup/computation prevents simultaneous metrics/day requests from duplicating the initial calculation; it also serializes first calculations for different cutoffs. No expensive work is preloaded during API startup.

Date requests filter the cached month in Europe/London and reset its index; callers receive a deep copy to protect cached DataFrames from mutation. Calculating summary metrics and serializing responses remain uncached because measurements show these costs are small. Only successful loads/calculations are cached, so missing/malformed-data failures can recover on retry. A new cutoff still pays for its first monthly calculation, including when requested by the daily endpoint alone.

The snapshot is immutable for the process lifetime: changing CSVs requires a backend restart (and rebuilding the bundled image in Docker). There is no file watcher or public invalidation endpoint. Each process has its own cache; ingestion must still run with the API stopped because loading two CSVs is not a transactional read.

Comparison output is an envelope with metadata, `sampleSize`, `totalHourlyIntervals`, and `records`. Records include UTC timestamps, MW values/errors, and forecast/actual status flags. JSON uses null rather than NaN or fabricated zeroes.

Metrics use only non-missing matched errors. MAE, RMSE, mean bias, over/underforecast rates, 90th-percentile absolute error, and maximum absolute error share `sampleSize`. The percentile uses pandas' default linear interpolation. Rates are fractions; an exact zero error contributes to neither directional rate. With no valid matches, metrics are null and sample size is zero.

`missingDayAheadForecasts` includes absent eligible publications, conflicts, and invalid selected values; separate counters explain these cases. Missing actual values include unusable/conflicting actuals. Forecast and actual missing counts can overlap. These counts describe hourly comparison targets, not full half-hour forecast coverage.

Query validation returns HTTP 422 for invalid hours/delivery dates. Missing or malformed CSVs map to HTTP 503; the health route remains independent of data availability. Current API error details can contain local filesystem paths. The configured Vercel API is public and unauthenticated; review error disclosure and access control before treating the historical demo as a production service.

## React request and presentation contract

Monthly metrics and selected-day comparisons have separate effects and AbortControllers. A date change leaves monthly metrics alone; a cutoff change refreshes both. Cleanup aborts superseded requests, and result keys hide stale-filter data before effects run. StrictMode may issue then cancel an initial development request; it is not an infinite request loop.

The comparison mapper reads `response.records`. Numeric formatters show N/A for null/non-finite values and preserve bias sign; rates are converted from fractions only for display. UK display timestamps use explicit `Intl` Europe/London formatters, never the viewer's machine timezone. The shared chart component is lazy-loaded; null points break forecast lines through `connectNulls={false}`. Loading, failure/retry, and empty-data states do not invent chart data.

The API owns forecast selection and metric calculations; React only presents them. There is no router or global state library because this is one page. No unused production dependency was identified in this review.

## Comparability review

### Data Comparability

Reviewed on October 25, 2026 using the official sources below. Current API descriptions, historical design intent, and observations from the stored January snapshot are distinguished deliberately.

| Question | Confirmed evidence / assessment |
| --- | --- |
| FUELHH meaning and units | GB generation outturn by fuel type, in average MW over each 30-minute interval; WIND selects the wind category. It is operational outturn, not an instantaneous reading or settlement MWh. [S1], [S3] |
| WINDFOR meaning and units | Wind generation forecast in MW for farms visible to the system operator with operational metering. Elexon's portal describes a forecast for a target time. [S1], [S2] |
| Geography / embedded generation | Both concern an operationally metered GB wind subset, not all UK wind or Northern Ireland. NESO identifies its FUELHH wind source as transmission-connected BMU operational metering and explains that embedded wind/solar estimates are separate. Unmetered embedded generation must not be assumed included in WINDFOR. [S1], [S3] |
| Same population? | Historical P220 design explicitly envisaged the metered wind forecast and fuel-type outturn being shown together, excluding wind without the Transmission Company's metering. This supports a related operational scope, but does not verify identical unit membership in January 2026. [S4] |
| Resolution in these files | FUELHH contains 1,488 half-hourly starts; WINDFOR contains 744 hourly target starts and multiple publications per target. Observed hourly spacing is not evidence of an hourly averaging window. |
| WINDFOR startTime / averaging | It is the forecast target time, distinct from publishTime. The current stream schema does not define a duration or averaging window. Historical specifications associate wind forecasts with Settlement Periods, while older message descriptions also use peak terminology. A definitive mapping from the January 2026 hourly stream target to an instantaneous value, hourly average, or specific half-hour average remains unresolved. [S1], [S2], [S4], [S5] |
| Curtailment / availability basis | FUELHH reflects operational outturn. The reviewed API definitions do not establish whether this WINDFOR series predicts unconstrained available generation or expected post-instruction output. The two reduced CSVs cannot reconcile curtailment, outages, or matching unit coverage. Unresolved. |

Official sources and evidence:

- **S1:** [Elexon OpenAPI schema](https://data.elexon.co.uk/swagger/v1/swagger.json), GET `/datasets/FUELHH/stream`, `/datasets/WINDFOR/stream`, `/forecast/generation/wind`, and associated schemas. The FUELHH route defines 30-minute average MW; the wind route defines the visible, operationally metered population. Field names alone do not establish time-window equivalence.
- **S2:** [Elexon developer portal](https://developer.data.elexon.co.uk/), API Patterns. The wind example identifies a target-time MW forecast; dataset endpoints preserve superseded publications. This does not specify the physical averaging window or prove actual trader receipt time.
- **S3:** [NESO Operational Transparency Forum, April 15, 2026](https://www.neso.energy/document/380351/download), printed pages 45-46, response to the April 1 demand-snapshot question. This identifies transmission-BMU operational metering for wind and separately published embedded estimates. It supports interpretation, not an audited January unit list.
- **S4:** [Elexon P220 requirements specification, November 2007](https://assets.elexon.co.uk/wp-content/uploads/sites/11/2012/02/28170955/p220as10.pdf), sections 3.2.1.2 and 3.2.2 (printed pages 8-10), plus section 3.3.3 (page 15). Historical design pairs forecast values with corresponding fuel-type Settlement Period outturn and describes operational, not settlement, metering. It is historical design evidence, not a current hourly-stream contract.
- **S5:** [Elexon NETA IDD Part 1, v57.0](https://bscdocs.elexon.co.uk/bsc-module/interface-definition-documents/neta-interface-definition-and-design-document-part-1-interfaces-with-bsc-parties-and-their-agents/v-57-0/id-67b34bd747c7f13fbb2970f5), WINDFOR message description. Its half-hour/peak terminology and legacy publication pattern require reconciliation with the hourly 2026 stream; do not treat the older wording as proof of hourly averages.

**Assessment:** the current exact-timestamp method is reproducible and defensible as an explicitly labeled proxy. It is not yet validated day-ahead procurement forecast accuracy. Historical provider design supports pairing with corresponding Settlement Period outturn; it does not justify automatically averaging the two half-hours of every hour. Complete target coverage and a large positive bias prove neither physical equivalence nor a mismatch.

**Proposed follow-up, not implemented:** obtain a provider-confirmed definition of the 2026 WINDFOR target period, operational unit population, and curtailment basis. If targets correspond to the half-hour starting at startTime, exact matching retains that provider-supported pairing, with hourly sampling clearly labeled. If a one-hour average is confirmed instead, separately propose matching it to a duration-weighted two-half-hour mean, excluding incomplete hours. An instantaneous or available-power forecast needs a correspondingly defined actual measure. These alternatives have different estimands and must not replace validated business logic silently. No extra electricity dataset was fetched for this review.

### January numerical validation

`backend/validate_data.py` reads only the existing CSVs, prints the full diagnostic JSON (including all 31 daily mean errors), checks stored baselines and every selected publication at cutoffs 00/10/23, and verifies unchanged CSV hashes. It is separate from production analytics and does not write source data. Run instructions belong in README.

Observed snapshot statistics (MW):

| Series | n | Minimum | Maximum | Mean | Median |
| --- | ---: | ---: | ---: | ---: | ---: |
| All half-hour actuals | 1,488 | 1,695 | 17,805 | 11,580.74 | 12,141 |
| All forecast revisions | 18,080 | 2,250 | 21,353 | 13,343.86 | 13,427.5 |
| Selected 10:00 forecasts | 744 | 2,721 | 21,279 | 13,421.03 | 13,465.5 |
| Actuals at those hourly targets | 744 | 1,728 | 17,805 | 11,581.11 | 12,160.5 |

All-revision statistics weight every publication and must not be interpreted as one forecast per hour. No negative, non-finite, zero, duplicate, off-grid, or missing expected source timestamps were found. Forecast expected targets are hourly, so this does not claim half-hour forecast coverage. There is no stored installed-capacity metadata: physical plausibility cannot be certified from these files. Of the selected forecasts, 198 exceed the highest observed January actual, which is a diagnostic flag, not an installed-capacity violation.

At 10:00, signed error min/p10/median/p90/max is -3,967 / -502.7 / +1,661 / +4,377 / +6,857 MW (n=744). Forecast/actual ratio min/median/mean/p90/max is 0.592 / 1.153 / 1.188 / 1.424 / 2.563; ratios exclude zero actuals (none in this snapshot). Pearson correlation is 0.9287. The exploratory forecast-on-actual fit has slope 1.0929 and intercept +764.3 MW; it is not a calibration or causal explanation. Daily mean errors are positive on 27/31 days, ranging from -744.38 MW on January 19 to +5,328.96 MW on January 28 (24 matches each day). This shows persistent but varying overforecasting, without identifying its cause.

| UK cutoff | Matched hours | Missing forecasts | MAE MW | RMSE MW | Bias MW |
| --- | ---: | ---: | ---: | ---: | ---: |
| 00:00 | 682 | 62 | 2,207.80 | 2,686.67 | +1,894.55 |
| 10:00 | 744 | 0 | 2,107.18 | 2,611.24 | +1,839.92 |
| 23:00 | 744 | 0 | 1,919.28 | 2,395.25 | +1,713.34 |

These checks reproduce the established baseline values without differences. At 00:00, every day's 22:00 and 23:00 targets lack an eligible forecast. On the common 682-target sample, MAE is 2,207.80 / 2,121.78 / 1,917.49 MW at cutoffs 00/10/23. Later decision times have lower observed error here, but no official purchasing deadline has been validated.

At the default cutoff, publication lead time spans 14.5-37.5 hours. Groups up to 24h / 24-36h / over 36h have n=310/372/62 and MAE=2,297.40/1,975.43/1,946.56 MW. This inverse association is confounded by delivery hour, dates, weather, and publication timing; it does not prove that longer leads improve forecasts. The script retains sample counts and the same-hour comparison across cutoffs to make these limits visible.

## Remaining quality limits

- A five-test Playwright suite now covers real-data startup/KPIs, filters/gaps, rapid changes, failure/retry, and empty/null test-only responses. It runs headless Chromium with a deliberately different browser timezone. This is a smoke suite, not exhaustive cross-browser or accessibility certification.
- The installed Starlette/httpx test integration emits a deprecation warning. Keep a deliberate dependency-compatibility update separate from domain logic changes.
- Dates and the dashboard's 1,488-source-record context are specific to the January snapshot. Supporting other periods requires an explicit scope change, not just exposing a wider date picker.

## Technical review readiness

Validation on October 25, 2026: 34 backend tests passed, 0 failed; all 5 Playwright smoke tests passed; the frontend production build passed. Live health, metrics/comparison requests and HTTP 422 parameter checks passed within the smoke suite. Numerical checks reproduced the established 00/10/23 cutoff baselines with no differences. Hashes confirmed that production ingestion, analytics, routes, dashboard/chart source and both CSVs were unchanged during this phase.

Classification: **Ready with clearly stated limitations** for a senior-engineer demonstration. Setup, source provenance, selection rules, missingness, API contracts and repeatable validation are documented. Procurement-grade claims remain blocked by the unresolved target-window, exact January fleet alignment and curtailment/availability definitions above. No comparison-method change was made or implicitly approved by these engineering checks.


## Performance review

Measured October 25, 2026 on this Windows machine, Docker Engine 29.9.2 / Compose 5.6.1, Linux production containers through Nginx at localhost:8080, and installed headless Google Chrome (1440 x 1000). Baselines were captured before source changes. API/stage numbers are medians of three sequential requests; browser numbers are one fresh browser context followed by two reloads. These are small local samples without network/CPU throttling, not production latency guarantees.

| Measurement | Before | After |
| --- | ---: | ---: |
| Backend process startup to health (median of 3) | 1308 ms | 1225 ms |
| Stopped Compose stack to health through Nginx (built images, Engine already running) | 6.46 s | 2.38 s |
| Initial shell DOM / first contentful paint | 84 / 176 ms | 79 / 172 ms |
| First dashboard charts, cold backend/browser caches | 2.48 s | 1.41 s |
| Subsequent dashboard charts, two reloads | 1.73-3.22 s | 0.41-0.42 s |
| Metrics API, cutoff 10, warm backend | 1833 ms | 22 ms |
| Daily comparison API, January 15 / cutoff 10, warm backend | 186 ms | 9 ms |
| Health API through Nginx | 6 ms | 22 ms |
| Nginx HTML response | 6 ms | 27 ms |
| Date change to charts, January 19 | 269 ms | 114 ms |
| First switch to uncached cutoff 00 / 23 | 2133 / 2233 ms | 765 / 817 ms |
| Return to cached cutoff 10 | 2300 ms | 100 ms |
| Cold JS transfer, both chunks, including resource timing overhead | 678 KB | 204 KB |

Stack start means `docker compose stop`, then timing `docker compose up -d` until `/api/health` responds through port 8080. Builds, image downloads, Docker Desktop/Engine launch, and a machine/filesystem cold boot are excluded; their cold-start times are **not measured**. Backend startup was measured separately by launching Uvicorn on an unused internal port and polling health every 10 ms. Initial backend health-check success occurred approximately 5.60 s after container start before, and 1.5-3 s in optimized runs; failed probes within the grace period did not cause restarts. Both services retained restart count zero.

The original bottleneck was repeated pandas work: CSV reading/parsing took about 6 ms for actuals and 40 ms for forecasts, monthly forecast selection 1136 ms, and full comparison construction 1515 ms. After the single-numeric-value fast path, uncached selection measured 695 ms and full comparison construction 679 ms. Duplicates/conflicts, strings, booleans, nulls and invalid values retain their validation path. Caching then avoids repeating monthly construction for the same cutoff. Metrics calculation itself remained about 2 ms and monthly record serialization about 14-16 ms, so neither a new serializer nor a separate metrics cache was justified.

Cold-page concurrent metrics/comparison requests originally took 2550 / 1805 ms; afterward both took about 934 ms while sharing the initial monthly calculation. At cutoff 00 / 10 / 23, warm metrics medians became 29 / 22 / 10 ms and daily comparison medians 11 / 9 / 9 ms. The higher health/HTML medians in the final sample show local scheduling variability; no speedup is claimed for those endpoints.

React already renders the shell and placeholders independently of the API. Main-JS download completion to shell DOM was 62 ms before and 52 ms after (an initialization proxy, not isolated React CPU time). Chart chunk download completion to observed chart paint was 501 ms before and 375 ms after; this includes module evaluation, React/Recharts work and browser scheduling, not isolated Recharts CPU time. Pure React/Recharts CPU durations were **not measured**. Shell/chart times used DOM observation and two animation frames; API/resource times used browser Resource Timing. No React refactor, blanket memoization or new dynamic import was justified. Existing lazy chart loading excludes the 394 KB chart chunk from the initial 284 KB JS bundle. Uncompressed bundle sizes and chart behavior are unchanged; Nginx gzip reduces transfer sizes.

Production made two initial requests (metrics + comparison), one for a date change and two for a cutoff change. A development StrictMode audit observed four initial requests, with the first two aborted and the remaining two completed. Cancellation and stale-filter protection remain intact; the backend lock also prevents duplicate initial calculation. StrictMode remains enabled.

Validation: 39 backend tests, all 5 existing browser smoke tests and the frontend production build pass. Complete metrics, daily and monthly comparison payloads at cutoffs 00/10/23 match the saved pre-change responses exactly; all 93 daily/monthly record consistency checks pass. The 10:00 baseline remains n=744, MAE 2107.176075 MW, RMSE 2611.242871 MW, bias +1839.920699 MW and overforecast rate 0.844086. Historical CSV hashes are unchanged. Compose builds/startup, Nginx syntax, real API forwarding, HTML revalidation, fingerprinted-asset caching and gzip headers were checked. Cache regressions cover cutoff/date separation, caller mutation, concurrent first requests, failed-load retry, nullable numeric validation and invalid filters.

The first request for any uncached cutoff still performs a monthly calculation; no prewarming shifts that work into startup. Caches persist until backend restart, and are independent per process. See README for refreshing bundled or local CSVs. The existing Starlette/httpx deprecation warning remains a separate dependency maintenance issue.
