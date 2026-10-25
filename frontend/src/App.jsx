import { lazy, Suspense, useEffect, useState } from 'react';
import MetricCard from './components/MetricCard';
import api from './services/api';
import { isNumber, mw, number, percent, ukDate } from './format';

const ForecastChart = lazy(() => import('./components/ForecastChart'));

function failureMessage(error) {
  if (error.response?.status === 503) return 'Historical data is unavailable. Check the backend CSV files, then retry.';
  if (error.response) return `The API returned HTTP ${error.response.status}. Check the backend and retry.`;
  if (error.code === 'ECONNABORTED') return 'The API took too long to respond. Check the backend and retry.';
  return `Could not load data from ${api.defaults.baseURL}. Check that the backend is running and allows this frontend origin.`;
}

function RequestState({ loading, error, empty, onRetry, compact = false }) {
  return (
    <div className={`request-state ${compact ? 'compact-state' : ''}`} role={error ? 'alert' : 'status'}>
      {loading ? <><span className="loader" aria-hidden="true" /><p>Loading historical observations…</p></> : error ? (
        <><span className="state-symbol" aria-hidden="true">!</span><h3>Unable to load this analysis</h3><p>{error}</p><button className="retry-button" onClick={onRetry}>Try again <span aria-hidden="true">↻</span></button></>
      ) : <><h3>{empty}</h3><p>Choose another date or forecast cutoff.</p></>}
    </div>
  );
}

export default function App() {
  const [showCutoffNote, setShowCutoffNote] = useState(false);
  const [date, setDate] = useState('2026-01-15');
  const [cutoff, setCutoff] = useState(10);
  const [metricsRetry, setMetricsRetry] = useState(0);
  const [comparisonRetry, setComparisonRetry] = useState(0);
  const [metricState, setMetricState] = useState({ key: null, loading: true });
  const [comparisonState, setComparisonState] = useState({ key: null, loading: true });
  const comparisonKey = `${date}/${cutoff}`;

  useEffect(() => {
    const controller = new AbortController();
    setMetricState({ key: cutoff, loading: true });
    api.get('/api/metrics', { params: { cutoff_hour: cutoff }, signal: controller.signal })
      .then(({ data }) => {
        if (!data || !isNumber(data.sampleSize)) throw new Error('Unexpected metrics response');
        if (!controller.signal.aborted) setMetricState({ key: cutoff, data, loading: false });
      })
      .catch((error) => {
        if (!controller.signal.aborted) setMetricState({ key: cutoff, loading: false, error: failureMessage(error) });
      });
    return () => controller.abort();
  }, [cutoff, metricsRetry]);

  useEffect(() => {
    const controller = new AbortController();
    setComparisonState({ key: comparisonKey, loading: true });
    api.get('/api/comparison', { params: { date, cutoff_hour: cutoff }, signal: controller.signal })
      .then(({ data }) => {
        if (!data || !Array.isArray(data.records)) throw new Error('Unexpected comparison response');
        if (!controller.signal.aborted) setComparisonState({ key: comparisonKey, data, loading: false });
      })
      .catch((error) => {
        if (!controller.signal.aborted) setComparisonState({ key: comparisonKey, loading: false, error: failureMessage(error) });
      });
    return () => controller.abort();
  }, [date, cutoff, comparisonKey, comparisonRetry]);

  // Hide results from earlier filters immediately, even before the next effect runs.
  const metricView = metricState.key === cutoff ? metricState : { loading: true };
  const dayView = comparisonState.key === comparisonKey ? comparisonState : { loading: true };
  const metrics = metricView.data;
  const day = dayView.data;
  const records = day?.records ?? [];
  const hasForecast = records.some((record) => isNumber(record.forecastMW));
  const hasErrors = records.some((record) => isNumber(record.errorMW));
  const cutoffLabel = `${String(cutoff).padStart(2, '0')}:00`;
  const kpis = [
    { label: 'Mean absolute error', value: number(metrics?.maeMW), unit: 'MW', description: 'Average forecast miss, in either direction.', accent: true },
    { label: 'Root mean squared error', value: number(metrics?.rmseMW), unit: 'MW', description: 'Gives greater weight to larger forecast misses.' },
    { label: 'Mean forecast bias', value: number(metrics?.meanBiasErrorMW, true), unit: 'MW', description: 'Positive: overforecast. Negative: underforecast.' },
    { label: 'Overforecast rate', value: percent(metrics?.overforecastRate), description: 'Share of matched hours with forecast above actual.' },
  ];

  const chartState = (errorChart = false) => dayView.loading || dayView.error || !(errorChart ? hasErrors : hasForecast) ? (
    <RequestState loading={dayView.loading} error={dayView.error} empty={errorChart ? 'No matched observations for this day' : 'No eligible forecasts for this day'} onRetry={() => setComparisonRetry((value) => value + 1)} />
  ) : <Suspense fallback={<RequestState loading />}><ForecastChart records={records} errorChart={errorChart} /></Suspense>;

  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">Skip to analysis</a>
      <header className="site-header">
        <div className="header-inner flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-4 py-3 sm:px-6 lg:px-9">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true"><svg viewBox="0 0 28 28" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M5 10h14a3 3 0 1 0-3-3M3 15h19a3 3 0 1 0-3-3M7 20h9a3 3 0 1 1-3 3" /></svg></span>
            <div><h1>Wind Intelligence</h1><p>Day-ahead wind analytics</p></div>
          </div>
          <p className="m-0 whitespace-nowrap text-[10px] text-[var(--muted)] sm:text-xs">Elexon · Jan 2026</p>
        </div>
      </header>
      <main id="main-content" className="dashboard">
        <div className="page-heading flex flex-col gap-4 md:flex-row md:flex-wrap md:items-end md:justify-between">
           <div><h2>Forecast performance</h2><p className="page-intro">Forecast vs actual wind generation</p></div>
           <section className="filter-controls grid w-full grid-cols-2 gap-3 md:w-auto md:grid-cols-[160px_132px] md:gap-4" aria-label="Analysis filters">
             <div className="flex min-w-0 flex-col gap-1.5">
               <label htmlFor="delivery-date">Delivery date</label>
               <input id="delivery-date" type="date" min="2026-01-01" max="2026-01-31" value={date} onChange={(event) => { const value = event.target.value; if (/^2026-01-\d{2}$/.test(value) && value >= '2026-01-01' && value <= '2026-01-31') setDate(value); }} />
             </div>
             <div className="flex min-w-0 flex-col gap-1.5">
               <div className="flex items-center gap-1.5">
                 <label htmlFor="decision-cutoff">Forecast cutoff</label>
             <span className="cutoff-hint" onMouseEnter={() => setShowCutoffNote(true)} onMouseLeave={() => setShowCutoffNote(false)}>
              <button type="button" className="info-button" aria-label="About the assumed forecast cutoff" aria-describedby="cutoff-assumption" onFocus={() => setShowCutoffNote(true)} onBlur={() => setShowCutoffNote(false)} onClick={() => setShowCutoffNote(true)} onKeyDown={(event) => { if (event.key === 'Escape') setShowCutoffNote(false); }}>i</button>
              <span id="cutoff-assumption" className="assumption-tooltip" role="tooltip" hidden={!showCutoffNote}>Assumed previous-day cutoff, UK time. Used to select forecasts available at the decision time.</span>
            </span>
               </div>
               <select id="decision-cutoff" value={cutoff} onChange={(event) => setCutoff(Number(event.target.value))}>{Array.from({ length: 24 }, (_, hour) => <option key={hour} value={hour}>{String(hour).padStart(2, '0')}:00</option>)}</select>
             </div>
           </section>
         </div>
 
         <section aria-labelledby="accuracy-title" className="accuracy-section">
          <div className="section-caption"><h2 id="accuracy-title">January accuracy</h2><p>Monthly metrics · {cutoffLabel} cutoff · {metricView.loading ? 'Loading sample…' : `${number(metrics?.sampleSize)} matched hourly observations`}</p></div>
          {metricView.error && <RequestState error={metricView.error} onRetry={() => setMetricsRetry((value) => value + 1)} compact />}
          <div className="metric-grid">{kpis.map((metric) => <MetricCard key={metric.label} {...metric} loading={metricView.loading} />)}</div>
        </section>

        <section className="panel forecast-panel" aria-labelledby="forecast-title" aria-busy={dayView.loading}>
          <div className="panel-heading"><div><p className="eyebrow">DELIVERY DAY DETAIL</p><h2 id="forecast-title">Forecast vs actual generation</h2><p>{ukDate(date)} · hourly comparisons</p></div><span className="day-count">{dayView.loading ? 'Loading…' : dayView.error ? 'Data unavailable' : `${number(day?.sampleSize)} / ${number(day?.totalHourlyIntervals)} matched hours`}</span></div>
          {chartState()}
        </section>

        <div className="lower-grid">
          <section className="panel" aria-labelledby="error-title" aria-busy={dayView.loading}>
            <div className="panel-heading"><div><p className="eyebrow">DIRECTION OF ERROR</p><h2 id="error-title">Forecast error</h2><p>{ukDate(date)} · forecast minus actual</p></div></div>
            <div className="error-legend"><span><i className="legend-dot positive" />Positive · overforecast</span><span><i className="legend-dot negative" />Negative · underforecast</span></div>
            {chartState(true)}
          </section>
          <section className="panel coverage-panel" aria-labelledby="coverage-title" aria-busy={metricView.loading}>
            <div className="panel-heading"><div><p className="eyebrow">THE MONTH IN CONTEXT</p><h2 id="coverage-title">Coverage & uncertainty</h2><p>January 2026 · {cutoffLabel} cutoff</p></div></div>
            {metricView.loading ? <RequestState loading compact /> : metricView.error ? <p className="coverage-unavailable">Monthly data unavailable. Use “Try again” above.</p> : (
              <>
                <div className="coverage-highlight"><strong>{number(metrics?.eligibleComparisonIntervals)}<span> / {number(metrics?.totalHourlyIntervals)}</span></strong><p>eligible hourly comparisons</p></div>
                <dl className="coverage-list">
                  <div><dt>Missing day-ahead forecasts</dt><dd className={metrics?.missingDayAheadForecasts > 0 ? 'risk-text' : ''}>{number(metrics?.missingDayAheadForecasts)}</dd></div>
                  <div><dt>Missing actual observations</dt><dd>{number(metrics?.missingActualValues)}</dd></div>
                  <div className="coverage-separator"><dt>90th percentile absolute error</dt><dd>{mw(metrics?.p90AbsoluteErrorMW)}</dd></div>
                  <div><dt>Maximum absolute error</dt><dd>{mw(metrics?.maxAbsoluteErrorMW)}</dd></div>
                </dl>
              </>
            )}
          </section>
        </div>

      </main>
    </div>
  );
}
