export default function MetricCard({ label, value, unit, description, loading, accent = false }) {
  return (
    <article className={`metric-card ${accent ? 'metric-accent' : ''}`} aria-busy={loading}>
      <h3>{label}</h3>
      {loading ? <div className="skeleton metric-skeleton" aria-label="Loading metric" /> : (
        <p className="metric-value">{value}<span>{value !== 'N/A' ? unit : ''}</span></p>
      )}
      <p className="metric-description">{description}</p>
    </article>
  );
}
