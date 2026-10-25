import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart,
  ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { mw, number, ukTime, ukTimestamp } from '../format';

const colors = { forecast: '#5264af', actual: '#0d8278', positive: '#b96728', negative: '#387cb1' };

function DetailTooltip({ active, payload, label, records }) {
  if (!active) return null;
  const row = payload?.[0]?.payload ?? records.find((record) => record.startTime === label);
  if (!row) return null;
  return (
    <div className="chart-tooltip">
      <p className="tooltip-title">{ukTimestamp(row.startTime)}</p>
      <dl>
        <div><dt>Forecast</dt><dd>{mw(row.forecastMW)}</dd></div>
        <div><dt>Actual</dt><dd>{mw(row.actualMW)}</dd></div>
        <div><dt>Forecast − actual</dt><dd>{mw(row.errorMW, true)}</dd></div>
        <div className="tooltip-divider"><dt>Published</dt><dd>{ukTimestamp(row.publishTime)}</dd></div>
        <div><dt>Forecast cutoff</dt><dd>{ukTimestamp(row.decisionCutoff)}</dd></div>
      </dl>
      {row.forecastStatus !== 'selected' && <p className="tooltip-note">Forecast unavailable: {row.forecastStatus}.</p>}
    </div>
  );
}

export default function ForecastChart({ records, errorChart = false }) {
  const axes = (
    <>
      <CartesianGrid stroke="#e8edf1" strokeDasharray="3 4" vertical={false} />
      <XAxis dataKey="startTime" tickFormatter={ukTime} minTickGap={28} tickLine={false} axisLine={false} tickMargin={12} tick={{ fill: '#697787', fontSize: 11 }} />
      <YAxis width={62} tickFormatter={(value) => number(value)} tickLine={false} axisLine={false} tick={{ fill: '#697787', fontSize: 11 }} />
      <Tooltip content={<DetailTooltip records={records} />} filterNull={false} cursor={{ stroke: '#bbc7d2', strokeDasharray: '4 4', fill: '#f0f4f7' }} />
    </>
  );
  return (
    <div className={`chart ${errorChart ? 'error-chart' : ''}`}>
      <p className="axis-unit">{errorChart ? 'Error' : 'Generation'} · MW</p>
      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
        {errorChart ? (
          <BarChart data={records} margin={{ top: 14, right: 12, bottom: 10, left: 0 }} accessibilityLayer>
            {axes}
            <ReferenceLine y={0} stroke="#8191a1" strokeWidth={1} />
            <Bar dataKey="errorMW" name="Forecast − actual" maxBarSize={24} radius={[3, 3, 0, 0]} isAnimationActive={false}>
              {records.map((record) => <Cell key={record.startTime} fill={record.errorMW < 0 ? colors.negative : colors.positive} />)}
            </Bar>
          </BarChart>
        ) : (
          <LineChart data={records} margin={{ top: 14, right: 12, bottom: 10, left: 0 }} accessibilityLayer>
            {axes}
            <Legend verticalAlign="top" align="right" height={30} iconType="plainline" iconSize={22} wrapperStyle={{ fontSize: 12, paddingBottom: 10 }} />
            <Line type="linear" dataKey="forecastMW" name="Day-ahead forecast" stroke={colors.forecast} strokeWidth={2.5} strokeDasharray="6 3" dot={false} activeDot={{ r: 4 }} connectNulls={false} isAnimationActive={false} />
            <Line type="linear" dataKey="actualMW" name="Actual generation" stroke={colors.actual} strokeWidth={2.5} dot={false} activeDot={{ r: 4 }} connectNulls={false} isAnimationActive={false} />
          </LineChart>
        )}
      </ResponsiveContainer>
      <p className="axis-time">Delivery time · UK</p>
    </div>
  );
}
