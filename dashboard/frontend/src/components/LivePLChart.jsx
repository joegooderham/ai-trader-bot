import { useApi } from '../hooks/useApi'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'

/**
 * Intraday P&L chart — shows hourly cumulative P&L for today.
 * Auto-refreshes every 30 seconds.
 */
export default function LivePLChart() {
  const { data } = useApi('/api/charts/pl-intraday', 30000)

  if (!data || data.length === 0) return null

  const isProfit = data[data.length - 1]?.cumulative_pl >= 0

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 mt-4">
      <h3 className="text-sm text-gray-500 uppercase tracking-wide mb-3">Today's P&L (Intraday)</h3>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            dataKey="hour"
            tick={{ fill: '#9ca3af', fontSize: 11 }}
            tickFormatter={h => `${String(h).padStart(2, '0')}:00`}
          />
          <YAxis
            tick={{ fill: '#9ca3af', fontSize: 11 }}
            tickFormatter={v => `£${v}`}
          />
          <Tooltip
            contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: 8 }}
            labelStyle={{ color: '#9ca3af' }}
            labelFormatter={h => `${String(h).padStart(2, '0')}:00 UTC`}
            formatter={(value, name) => [
              `£${value.toFixed(2)}`,
              name === 'cumulative_pl' ? 'Cumulative' : 'Hourly',
            ]}
          />
          <ReferenceLine y={0} stroke="#4b5563" strokeDasharray="3 3" />
          <Line
            type="monotone"
            dataKey="cumulative_pl"
            stroke={isProfit ? '#22c55e' : '#ef4444'}
            strokeWidth={2}
            dot={{ fill: isProfit ? '#22c55e' : '#ef4444', r: 3 }}
            activeDot={{ r: 5 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
