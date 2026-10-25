import { test, expect } from '@playwright/test';
import { number, percent, ukTimestamp } from '../src/format.js';

const backend = 'http://localhost:8000';
const api = (url) => url.startsWith(`${backend}/api/`);
const kpi = (page, label) => page.locator('.metric-card').filter({ has: page.getByRole('heading', { name: label, exact: true }) }).locator('.metric-value');

async function json(request, path) {
  const response = await request.get(`${backend}${path}`);
  expect(response.ok(), `${path}: HTTP ${response.status()}`).toBeTruthy();
  return response.json();
}

async function loaded(page) {
  await expect(page.locator('.recharts-wrapper')).toHaveCount(2);
  await expect(page.locator('.day-count')).toContainText('matched hours');
}

test('startup, real KPIs, API baselines, chart values and tooltips', async ({ page, request }) => {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  expect(await json(request, '/api/health')).toEqual({ status: 'ok', message: 'Wind Forecast API is running' });
  const metrics = await json(request, '/api/metrics?cutoff_hour=10');
  const comparison = await json(request, '/api/comparison?date=2026-01-15&cutoff_hour=10');
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Wind Intelligence', exact: true })).toBeVisible();
  await expect(kpi(page, 'Mean absolute error')).toContainText(number(metrics.maeMW));
  await expect(kpi(page, 'Root mean squared error')).toContainText(number(metrics.rmseMW));
  await expect(kpi(page, 'Mean forecast bias')).toContainText(number(metrics.meanBiasErrorMW, true));
  await expect(kpi(page, 'Overforecast rate')).toHaveText(percent(metrics.overforecastRate));
  await loaded(page);
  await expect(page.locator('#delivery-date')).toHaveValue('2026-01-15');
  await expect(page.locator('#delivery-date')).toHaveAttribute('min', '2026-01-01');
  await expect(page.locator('#delivery-date')).toHaveAttribute('max', '2026-01-31');
  await expect(page.locator('#decision-cutoff option')).toHaveCount(24);
  await expect(page.locator('.day-count')).toHaveText(`${comparison.sampleSize} / ${comparison.totalHourlyIntervals} matched hours`);
  const forecast = page.locator('.forecast-panel .recharts-line-curve');
  expect(await forecast.nth(0).getAttribute('stroke')).not.toBe(await forecast.nth(1).getAttribute('stroke'));
  await expect(forecast.nth(0)).toHaveAttribute('stroke-dasharray', '6 3');
  const chart = page.locator('.forecast-panel .recharts-wrapper');
  const box = await chart.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + 140);
  const tooltip = page.locator('.forecast-panel .chart-tooltip');
  await expect(tooltip).toBeVisible();
  const text = await tooltip.innerText();
  const selected = comparison.records.find((row) => text.includes(ukTimestamp(row.startTime)));
  expect(selected).toBeDefined();
  expect(text).toContain(number(selected.forecastMW));
  expect(text).toContain(number(selected.actualMW));
  expect(text).toContain(number(selected.errorMW, true));
  expect(text).toContain(ukTimestamp(selected.publishTime));
  expect(text).toContain(ukTimestamp(selected.decisionCutoff));
  await expect(page.locator('.error-chart .recharts-reference-line')).toHaveCount(1);
  await expect(page.getByRole('combobox', { name: 'Forecast cutoff', exact: true })).toBeVisible();
   await expect(page.getByText('Day-ahead wind analytics', { exact: true })).toBeVisible();
   await expect(page.getByText('Elexon \u00b7 Jan 2026', { exact: true })).toBeVisible();
   await expect(page.getByText('Forecast vs actual wind generation', { exact: true })).toBeVisible();
   await expect(page.locator('.filter-panel, .period-label, .historical-badge')).toHaveCount(0);
   const titleBounds = await page.getByRole('heading', { name: 'Forecast performance', exact: true }).boundingBox();
   const filterBounds = await page.getByRole('region', { name: 'Analysis filters' }).boundingBox();
   expect(filterBounds.x).toBeGreaterThan(titleBounds.x + titleBounds.width);
   expect(filterBounds.y).toBeLessThan(titleBounds.y + titleBounds.height);
   const dateBounds = await page.locator('#delivery-date').boundingBox();
   const cutoffBounds = await page.locator('#decision-cutoff').boundingBox();
   expect(dateBounds.height).toBe(cutoffBounds.height);
  const cutoffInfo = page.getByRole('button', { name: 'About the assumed forecast cutoff' });
  await cutoffInfo.focus();
  await expect(page.getByRole('tooltip')).toHaveText('Assumed previous-day cutoff, UK time. Used to select forecasts available at the decision time.');
  await cutoffInfo.press('Escape');
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await cutoffInfo.click();
  await expect(page.getByRole('tooltip')).toBeVisible();
  await cutoffInfo.press('Tab');
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Methodology', exact: true })).toHaveCount(0);
  await expect(page.getByText('Comparison notes', { exact: true })).toHaveCount(0);
  await expect(page.locator('footer')).toHaveCount(0);
  await expect(page.locator('main > :last-child')).toHaveClass('lower-grid');
  const bottomGap = await page.locator('main').evaluate((main) =>
    main.getBoundingClientRect().bottom - main.lastElementChild.getBoundingClientRect().bottom - parseFloat(getComputedStyle(main).paddingBottom));
  expect(Math.abs(bottomGap)).toBeLessThanOrEqual(1);
  expect(errors).toEqual([]);
});

test('date/cutoff filters, gaps, signed bars, invalid inputs and mobile layout', async ({ page, request }) => {
  const metricRequests = [];
  page.on('request', (request) => { if (request.url().includes('/api/metrics')) metricRequests.push(request.url()); });
  await page.goto('/');
  await loaded(page);
  const before = metricRequests.length;
  await page.locator('#delivery-date').fill('2026-01-19');
  await loaded(page);
  await expect(page.locator('.forecast-panel .panel-heading')).toContainText('19 January 2026');
  expect(metricRequests).toHaveLength(before);
  const response = page.waitForResponse((response) => response.url().includes('/api/comparison?date=2026-01-19&cutoff_hour=0') && response.ok());
  await page.locator('#decision-cutoff').selectOption('0');
  const body = await (await response).json();
  await expect(page.locator('.day-count')).toHaveText('22 / 24 matched hours');
  const zero = await json(request, '/api/metrics?cutoff_hour=0');
  await expect(kpi(page, 'Mean absolute error')).toContainText(number(zero.maeMW));
  await expect(page.locator('.coverage-highlight')).toContainText(number(zero.sampleSize));
  expect(body.records.filter((row) => row.forecastMW === null)).toHaveLength(2);
  const curves = page.locator('.forecast-panel .recharts-line-curve');
  const forecastBounds = await curves.nth(0).boundingBox();
  const actualBounds = await curves.nth(1).boundingBox();
  expect(forecastBounds.width).toBeLessThan(actualBounds.width); // Null late-hour forecasts are not extended.

  const bars = page.locator('.error-chart .recharts-bar-rectangle path');
  await expect.poll(() => bars.count()).toBeGreaterThan(0);
  const fills = await bars.evaluateAll((nodes) => nodes.map((node) => node.getAttribute('fill')));
  expect(fills).toContain('#b96728');
  expect(fills).toContain('#387cb1');
  await page.locator('#delivery-date').fill('2026-02-01');
  await expect(page.locator('#delivery-date')).toHaveValue('2026-01-19');
  await expect(page.locator('#decision-cutoff')).toHaveValue('0');
  for (const width of [768, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
     if (width < 768) {
       const intro = await page.locator('.page-heading > div').boundingBox();
       const filters = await page.getByRole('region', { name: 'Analysis filters' }).boundingBox();
       expect(filters.y).toBeGreaterThanOrEqual(intro.y + intro.height);
       await page.getByRole('button', { name: 'About the assumed forecast cutoff' }).click();
       const hint = await page.getByRole('tooltip').boundingBox();
       expect(hint.x).toBeGreaterThanOrEqual(0);
       expect(hint.x + hint.width).toBeLessThanOrEqual(width);
       await page.getByRole('button', { name: 'About the assumed forecast cutoff' }).press('Escape');
     }
  }
  for (const hour of [0, 10, 23]) {
    const summary = await json(request, `/api/metrics?cutoff_hour=${hour}`);
    expect(summary.sampleSize).toBe(hour === 0 ? 682 : 744);
    expect(summary.missingDayAheadForecasts).toBe(hour === 0 ? 62 : 0);
  }
  for (const path of ['/api/metrics?cutoff_hour=24', '/api/comparison?cutoff_hour=-1', '/api/comparison?date=2026-02-01']) {
    expect((await request.get(`${backend}${path}`)).status()).toBe(422);
  }
});

test('rapid filter changes cancel older work and retain the newest result', async ({ page, request }) => {
  await page.goto('/');
  await loaded(page);
  let release;
  let reached;
  let finishOldResponse;
  const oldResponseFinished = new Promise((resolve) => { finishOldResponse = resolve; });
  const gate = new Promise((resolve) => { release = resolve; });
  const intercepted = new Promise((resolve) => { reached = resolve; });
  await page.route('**/api/comparison?date=2026-01-16&cutoff_hour=10', async (route) => {
    const response = await route.fetch();
    reached();
    await gate;
    try { await route.fulfill({ response }); } catch { /* The canceled request may already be disposed. */ } finally { finishOldResponse(); }
  });
  await page.locator('#delivery-date').fill('2026-01-16');
  await intercepted;
  await expect(page.getByRole('status').filter({ hasText: 'Loading historical observations' }).first()).toBeVisible();
  await page.locator('#decision-cutoff').selectOption('23');
  await page.locator('#delivery-date').fill('2026-01-19');
  await loaded(page);
  const latest = await json(request, '/api/metrics?cutoff_hour=23');
  await expect(kpi(page, 'Mean absolute error')).toContainText(number(latest.maeMW));
  await expect(page.locator('.forecast-panel .panel-heading')).toContainText('19 January 2026');
  release();
  await oldResponseFinished;
  await page.waitForLoadState('networkidle');
  await loaded(page);
  await page.unroute('**/api/comparison?date=2026-01-16&cutoff_hour=10');
  await expect(page.locator('#delivery-date')).toHaveValue('2026-01-19');
  await expect(page.locator('#decision-cutoff')).toHaveValue('23');
  await expect(page.locator('.forecast-panel .panel-heading')).toContainText('19 January 2026');
});

test('backend unavailable and HTTP errors are visible and retry recovers', async ({ page }) => {
  await page.route('**/api/*', (route) => api(route.request().url()) ? route.abort('connectionrefused') : route.continue());
  await page.goto('/');
  await expect(page.getByRole('alert').first()).toContainText('Could not load data');
  await expect(kpi(page, 'Mean absolute error')).toHaveText('N/A');
  await page.unroute('**/api/*');
  await page.getByRole('button', { name: 'Try again' }).first().click();
  await page.getByRole('button', { name: 'Try again' }).first().click();
  await loaded(page);
  await page.route('**/api/comparison*', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Test-only data unavailable' }) }));
  await page.locator('#delivery-date').fill('2026-01-16');
  await expect(page.getByRole('alert').first()).toContainText('Historical data is unavailable');
  await expect(page.locator('.recharts-wrapper')).toHaveCount(0);
  await page.unroute('**/api/comparison*');
  await page.route('**/api/comparison*', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: '{}' }));
  await page.getByRole('button', { name: 'Try again' }).first().click();
  await expect(page.getByRole('alert').first()).toContainText('HTTP 500');
});

test('test-only empty envelope and null measurements show unavailable states', async ({ page, request }) => {
  // Only empty/null fixtures are intercepted. No fabricated generation is charted or written to CSV.
  const comparison = await json(request, '/api/comparison?date=2026-01-15&cutoff_hour=10');
  const metrics = await json(request, '/api/metrics?cutoff_hour=10');
  await page.route('**/api/comparison*', (route) => route.fulfill({ json: { ...comparison, sampleSize: 0, records: [] } }));
  await page.route('**/api/metrics*', (route) => route.fulfill({ json: { ...metrics, sampleSize: 0, maeMW: null, rmseMW: null, meanBiasErrorMW: null, overforecastRate: null } }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'No eligible forecasts for this day' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'No matched observations for this day' })).toBeVisible();
  await expect(kpi(page, 'Mean absolute error')).toHaveText('N/A');
  await expect(kpi(page, 'Mean forecast bias')).toHaveText('N/A');
  await expect(kpi(page, 'Overforecast rate')).toHaveText('N/A');
  await expect(page.locator('.recharts-wrapper')).toHaveCount(0);
});
