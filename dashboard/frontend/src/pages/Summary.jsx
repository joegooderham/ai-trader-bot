import { useApi } from '../hooks/useApi'
import StatCard from '../components/StatCard'
import PLBadge from '../components/PLBadge'

export default function Summary() {
  // Refresh every 60 seconds — plan data changes once daily, stats change on trade close
  const { data, loading, error } = useApi('/api/summary', 60000)

  if (loading) return <LoadingSkeleton />
  if (error) return <ErrorMessage error={error} />
  if (data?.error) return <ErrorMessage error={data.error} />

  const { week, month, all_time, pairs, daily_trend, plan } = data

  return (
    <div>
      <h2 className="text-xl md:text-2xl font-bold mb-4 md:mb-6">Trading Summary</h2>

      {/* Performance cards: 7d / 30d / all-time */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 md:gap-4 mb-6">
        <PeriodCard label="Last 7 Days" stats={week} />
        <PeriodCard label="Last 30 Days" stats={month} />
        <PeriodCard label="All Time" stats={all_time} />
      </div>

      {/* Weekly detail cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4 mb-6">
        <StatCard label="Best Trade" value={<PLBadge value={week.best_trade} />} sub="This week" />
        <StatCard label="Worst Trade" value={<PLBadge value={week.worst_trade} />} sub="This week" />
        <StatCard label="Gross Profit" value={`\u00A3${week.gross_profit}`} sub="This week" />
        <StatCard label="Gross Loss" value={`\u00A3${week.gross_loss}`} sub="This week" />
      </div>

      {/* Daily P&L trend (7 days) */}
      {daily_trend?.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 md:p-4 mb-6">
          <h3 className="text-sm text-gray-500 uppercase tracking-wide mb-3">Daily P&L (7 Days)</h3>
          <div className="space-y-2">
            {daily_trend.map((d) => (
              <div key={d.date} className="flex items-center justify-between text-sm">
                <span className="text-gray-400 w-24">{d.date.slice(5)}</span>
                <div className="flex-1 mx-3">
                  <div
                    className={`h-4 rounded ${d.pl >= 0 ? 'bg-green-600/40' : 'bg-red-600/40'}`}
                    style={{
                      width: `${Math.min(Math.abs(d.pl) / Math.max(...daily_trend.map(x => Math.abs(x.pl)), 1) * 100, 100)}%`,
                      minWidth: '4px',
                    }}
                  />
                </div>
                <span className={`font-mono w-20 text-right ${d.pl >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {d.pl >= 0 ? '+' : ''}{'\u00A3'}{d.pl.toFixed(2)}
                </span>
                <span className="text-gray-500 text-xs w-16 text-right">{d.trades} trade{d.trades !== 1 ? 's' : ''}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Per-pair performance */}
      {pairs?.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 md:p-4 mb-6">
          <h3 className="text-sm text-gray-500 uppercase tracking-wide mb-3">Pair Performance (7 Days)</h3>

          {/* Desktop table */}
          <div className="hidden md:block">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-500 text-left border-b border-gray-800">
                  <th className="pb-2">Pair</th>
                  <th className="pb-2">Trades</th>
                  <th className="pb-2">Win Rate</th>
                  <th className="pb-2 text-right">Net P&L</th>
                </tr>
              </thead>
              <tbody>
                {pairs.map((p) => (
                  <tr key={p.pair} className="border-b border-gray-800/50">
                    <td className="py-2 font-medium text-white">{p.pair?.replace('_', '/')}</td>
                    <td className="py-2 text-gray-400">{p.trades}</td>
                    <td className="py-2">
                      <span className={p.win_rate >= 50 ? 'text-profit' : 'text-loss'}>
                        {p.win_rate}%
                      </span>
                    </td>
                    <td className="py-2 text-right font-mono">
                      <PLBadge value={p.net_pl} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile cards */}
          <div className="md:hidden space-y-2">
            {pairs.map((p) => (
              <div key={p.pair} className="bg-gray-800/50 rounded p-3 flex justify-between items-center">
                <div>
                  <span className="font-medium text-white">{p.pair?.replace('_', '/')}</span>
                  <span className="text-xs text-gray-500 ml-2">{p.trades} trades, {p.win_rate}% WR</span>
                </div>
                <PLBadge value={p.net_pl} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Next-day plan from Claude AI */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 md:p-4">
        <h3 className="text-sm text-gray-500 uppercase tracking-wide mb-3">Next Day Outlook</h3>
        {plan ? (
          <>
            <div className="text-xs text-gray-500 mb-3">
              Generated: {plan.generated_at?.slice(0, 16)} UTC
            </div>
            <div className="prose prose-invert prose-sm max-w-none whitespace-pre-wrap text-gray-300 leading-relaxed">
              {plan.text}
            </div>
          </>
        ) : (
          <p className="text-gray-500 text-sm">
            No plan generated yet. The bot generates a plan each evening after market close.
          </p>
        )}
      </div>
    </div>
  )
}

function PeriodCard({ label, stats }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <h4 className="text-xs text-gray-500 uppercase tracking-wide mb-2">{label}</h4>
      <div className="text-xl font-bold mb-1">
        <PLBadge value={stats.net_pl} />
      </div>
      <div className="text-xs text-gray-400">
        {stats.trades} trades &middot; {stats.win_rate}% win rate
      </div>
      <div className="text-xs text-gray-500 mt-1">
        {stats.wins}W / {stats.losses}L
      </div>
    </div>
  )
}

function LoadingSkeleton() {
  return (
    <div>
      <h2 className="text-xl md:text-2xl font-bold mb-4 md:mb-6">Trading Summary</h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 md:gap-4">
        {[...Array(3)].map((_, i) => (
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
      <p className="text-red-400 font-medium">Failed to load summary</p>
      <p className="text-red-500 text-sm mt-1">{error}</p>
    </div>
  )
}
