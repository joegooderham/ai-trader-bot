import { useApi } from '../hooks/useApi'
import PLBadge from '../components/PLBadge'

const SESSION_INFO = {
  Sydney:     { hours: '22:00 - 07:00 UTC', emoji: '🌏' },
  Tokyo:      { hours: '00:00 - 09:00 UTC', emoji: '🗼' },
  London:     { hours: '08:00 - 17:00 UTC', emoji: '🏛' },
  'New York': { hours: '13:00 - 22:00 UTC', emoji: '🗽' },
}

export default function SessionAnalysis() {
  const { data, loading, error } = useApi('/api/analytics/sessions')

  if (loading) return <div className="text-gray-500">Loading session data...</div>
  if (error) return <div className="text-red-400">Failed to load sessions: {error}</div>

  const sessions = data || {}

  return (
    <div>
      <h2 className="text-2xl font-bold mb-2">Session Performance</h2>
      <p className="text-sm text-gray-500 mb-6">
        Which forex sessions are most profitable for each pair.
      </p>

      {Object.keys(sessions).length === 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-8 text-center text-gray-500">
          No trade data yet.
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        {Object.entries(SESSION_INFO).map(([name, info]) => {
          const session = sessions[name]
          if (!session) return null

          return (
            <div key={name} className="bg-gray-900 border border-gray-800 rounded-lg p-5">
              {/* Header */}
              <div className="flex items-center justify-between mb-3">
                <div>
                  <h3 className="text-white font-medium text-lg">
                    {info.emoji} {name}
                  </h3>
                  <p className="text-xs text-gray-500">{info.hours}</p>
                </div>
                <div className="text-right">
                  <PLBadge value={session.pl || 0} />
                  <p className="text-xs text-gray-500 mt-1">
                    {session.trades} trades | {session.win_rate}% win
                  </p>
                </div>
              </div>

              {/* Per-pair breakdown */}
              {session.pairs && Object.keys(session.pairs).length > 0 ? (
                <div className="space-y-2">
                  {Object.entries(session.pairs)
                    .sort((a, b) => b[1].pl - a[1].pl)
                    .map(([pair, stats]) => (
                      <div key={pair} className="flex items-center gap-2">
                        <span className="text-xs text-gray-400 w-16">{pair.replace('_', '/')}</span>
                        <div className="flex-1 h-4 bg-gray-800 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full ${
                              stats.win_rate >= 50 ? 'bg-green-600/60' : 'bg-red-600/60'
                            }`}
                            style={{ width: `${Math.max(stats.win_rate, 5)}%` }}
                          />
                        </div>
                        <span className="text-xs text-gray-400 w-10 text-right">{stats.win_rate}%</span>
                        <span className={`text-xs font-mono w-14 text-right ${
                          stats.pl >= 0 ? 'text-green-400' : 'text-red-400'
                        }`}>
                          {stats.pl >= 0 ? '+' : ''}£{stats.pl.toFixed(0)}
                        </span>
                      </div>
                    ))}
                </div>
              ) : (
                <p className="text-xs text-gray-600">No trades in this session.</p>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
