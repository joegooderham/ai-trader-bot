import { useApi } from '../hooks/useApi'
import StatCard from '../components/StatCard'
import PLBadge from '../components/PLBadge'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'

export default function Overview() {
  // Auto-refresh every 30 seconds
  const { data, loading, error } = useApi('/api/overview', 30000)
  const { data: chartData } = useApi('/api/charts/pl-history?days=30')
  const { data: liveData } = useApi('/api/positions/live', 30000)

  if (loading) return <LoadingSkeleton />
  if (error) return <ErrorMessage error={error} />

  const { today, open_positions, all_time } = data

  return (
    <div>
      <h2 className="text-xl md:text-2xl font-bold mb-4 md:mb-6">Overview</h2>

      {/* DB status warning */}
      {data.db_status && (
        <div className="bg-yellow-900/20 border border-yellow-800 rounded-lg p-3 mb-4 text-yellow-400 text-sm">
          Database: {data.db_status}
        </div>
      )}

      {/* Today's stats */}
      <h3 className="text-sm text-gray-500 uppercase tracking-wide mb-3">Today — {today.date}</h3>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4 mb-6 md:mb-8">
        <StatCard
          label="Net P&L"
          value={<PLBadge value={today.net_pl} />}
          sub={`${today.trades} trades`}
        />
        <StatCard
          label="Win Rate"
          value={today.closed > 0 ? `${today.win_rate}%` : '\u2014'}
          sub={`${today.wins}W / ${today.losses}L`}
        />
        <StatCard
          label="Open Positions"
          value={open_positions.length}
          sub={open_positions.length > 0
            ? open_positions.map(p => p.pair?.replace('_', '/')).join(', ')
            : 'None'}
        />
        <StatCard
          label="Unrealized P&L"
          value={<PLBadge value={liveData?.total_unrealized_pl || 0} />}
          sub={liveData?.prices_available ? 'Live prices via yfinance' : 'Prices unavailable'}
        />
      </div>

      {/* P&L Chart */}
      {chartData?.data?.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 md:p-4 mb-6 md:mb-8">
          <h3 className="text-sm text-gray-500 uppercase tracking-wide mb-4">Cumulative P&L (30 days)</h3>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={chartData.data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis
                dataKey="date"
                tick={{ fill: '#9ca3af', fontSize: 11 }}
                tickFormatter={d => d.slice(5)}
              />
              <YAxis
                tick={{ fill: '#9ca3af', fontSize: 11 }}
                tickFormatter={v => `\u00A3${v}`}
                width={50}
              />
              <Tooltip
                contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: '8px' }}
                labelStyle={{ color: '#9ca3af' }}
                formatter={(value) => [`\u00A3${value.toFixed(2)}`, 'Cumulative P&L']}
              />
              <Line
                type="monotone"
                dataKey="cumulative_pl"
                stroke="#3b82f6"
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Open positions table */}
      {open_positions.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 md:p-4">
          <h3 className="text-sm text-gray-500 uppercase tracking-wide mb-4">Open Positions</h3>

          {/* Desktop table */}
          <div className="hidden md:block">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-500 text-left border-b border-gray-800">
                  <th className="pb-2">Pair</th>
                  <th className="pb-2">Direction</th>
                  <th className="pb-2">Entry</th>
                  <th className="pb-2">Size</th>
                  <th className="pb-2">Current</th>
                  <th className="pb-2">P&L</th>
                  <th className="pb-2">Opened</th>
                </tr>
              </thead>
              <tbody>
                {(liveData?.positions || open_positions).map((pos, i) => (
                  <tr key={i} className="border-b border-gray-800/50">
                    <td className="py-2 font-medium text-white">{pos.pair?.replace('_', '/')}</td>
                    <td className={`py-2 ${pos.direction === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                      {pos.direction}
                    </td>
                    <td className="py-2 font-mono">{pos.fill_price}</td>
                    <td className="py-2">{pos.size} lot{pos.size !== 1 ? 's' : ''}</td>
                    <td className="py-2 font-mono">{pos.current_price?.toFixed(pos.pair?.includes('JPY') ? 3 : 5) ?? '\u2014'}</td>
                    <td className="py-2">{pos.unrealized_pl != null ? <PLBadge value={pos.unrealized_pl} /> : '\u2014'}</td>
                    <td className="py-2 text-gray-400">{pos.opened_at?.slice(0, 16)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile cards */}
          <div className="md:hidden space-y-3">
            {(liveData?.positions || open_positions).map((pos, i) => (
              <div key={i} className="bg-gray-800/50 rounded p-3">
                <div className="flex justify-between items-center mb-1">
                  <span className="font-medium text-white">{pos.pair?.replace('_', '/')}</span>
                  {pos.unrealized_pl != null ? <PLBadge value={pos.unrealized_pl} /> : (
                    <span className={pos.direction === 'BUY' ? 'text-profit' : 'text-loss'}>
                      {pos.direction}
                    </span>
                  )}
                </div>
                <div className="text-xs text-gray-400">
                  Entry: <span className="font-mono">{pos.fill_price}</span> | Size: {pos.size} lot{pos.size !== 1 ? 's' : ''}
                  {pos.current_price && <> | Now: <span className="font-mono">{pos.current_price.toFixed(pos.pair?.includes('JPY') ? 3 : 5)}</span></>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function LoadingSkeleton() {
  return (
    <div>
      <h2 className="text-xl md:text-2xl font-bold mb-4 md:mb-6">Overview</h2>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="bg-gray-900 border border-gray-800 rounded-lg p-4 animate-pulse">
            <div className="h-3 bg-gray-800 rounded w-20 mb-3" />
            <div className="h-7 bg-gray-800 rounded w-24" />
          </div>
        ))}
      </div>
    </div>
  )
}

function ErrorMessage({ error }) {
  return (
    <div className="bg-red-900/20 border border-red-800 rounded-lg p-4">
      <p className="text-red-400 font-medium">Failed to load dashboard data</p>
      <p className="text-red-500 text-sm mt-1">{error}</p>
      <button
        onClick={() => window.location.reload()}
        className="mt-3 px-3 py-1.5 bg-red-800 text-red-200 rounded text-sm hover:bg-red-700"
      >
        Retry
      </button>
    </div>
  )
}
