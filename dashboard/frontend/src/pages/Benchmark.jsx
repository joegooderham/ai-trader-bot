import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import StatCard from '../components/StatCard'
import PLBadge from '../components/PLBadge'

export default function Benchmark() {
  const [days, setDays] = useState(30)
  const { data, loading } = useApi(`/api/analytics/benchmark?days=${days}`)

  return (
    <div>
      <h2 className="text-2xl font-bold mb-2">Performance Benchmark</h2>
      <p className="text-sm text-gray-500 mb-4">
        Bot trading returns vs buy-and-hold per pair. Positive alpha means the bot adds value.
      </p>

      <div className="flex gap-2 mb-6">
        {[7, 14, 30, 90].map(d => (
          <button
            key={d}
            onClick={() => setDays(d)}
            className={`px-3 py-1.5 text-xs rounded transition-colors ${
              days === d
                ? 'bg-blue-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
            }`}
          >
            {d}d
          </button>
        ))}
      </div>

      {loading && <div className="text-gray-500">Loading benchmark...</div>}

      {data && (
        <>
          <div className="grid md:grid-cols-2 gap-4 mb-6">
            <StatCard
              label={`Bot P&L (${days}d)`}
              value={<PLBadge value={data.total_bot_pl || 0} />}
              sub={`${data.pairs?.length || 0} pairs traded`}
            />
            <StatCard
              label="Verdict"
              value={data.total_bot_pl > 0 ? 'Outperforming' : 'Underperforming'}
              sub={data.total_bot_pl > 0 ? 'Bot is adding value' : 'Strategy needs tuning'}
              className={data.total_bot_pl > 0 ? 'border-green-800' : 'border-red-800'}
            />
          </div>

          <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-500 text-left border-b border-gray-800">
                  <th className="px-4 py-3">Pair</th>
                  <th className="px-4 py-3">Trades</th>
                  <th className="px-4 py-3">Win Rate</th>
                  <th className="px-4 py-3">Bot P&L</th>
                  <th className="px-4 py-3">Hold %</th>
                </tr>
              </thead>
              <tbody>
                {(data.pairs || []).map(p => (
                  <tr key={p.pair} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="px-4 py-2.5 font-medium text-gray-300">
                      {p.pair.replace('_', '/')}
                    </td>
                    <td className="px-4 py-2.5 text-gray-400">
                      {p.trades} ({p.wins}W)
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={p.win_rate >= 50 ? 'text-green-400' : 'text-red-400'}>
                        {p.win_rate}%
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <PLBadge value={p.bot_pl} />
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`font-mono text-sm ${
                        p.hold_pct_change > 0 ? 'text-green-400' : p.hold_pct_change < 0 ? 'text-red-400' : 'text-gray-500'
                      }`}>
                        {p.hold_pct_change > 0 ? '+' : ''}{p.hold_pct_change}%
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
