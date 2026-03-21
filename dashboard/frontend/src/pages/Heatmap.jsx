import { useApi } from '../hooks/useApi'

const HOURS = Array.from({ length: 24 }, (_, i) => i)

export default function Heatmap() {
  const { data, loading, error } = useApi('/api/analytics/heatmap')

  if (loading) return <div className="text-gray-500">Loading heatmap...</div>
  if (error) return <div className="text-red-400">Failed to load heatmap: {error}</div>
  if (!data || Object.keys(data).length === 0) {
    return (
      <div>
        <h2 className="text-2xl font-bold mb-6">Pair x Hour Heatmap</h2>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-8 text-center text-gray-500">
          No trade data yet. The heatmap will populate as trades are recorded.
        </div>
      </div>
    )
  }

  const pairs = Object.keys(data).sort()

  function getColor(cell) {
    if (!cell || cell.trades === 0) return 'bg-gray-900'
    const wr = cell.win_rate
    if (wr >= 70) return 'bg-green-600/60'
    if (wr >= 55) return 'bg-green-600/30'
    if (wr >= 45) return 'bg-gray-700'
    if (wr >= 30) return 'bg-red-600/30'
    return 'bg-red-600/60'
  }

  return (
    <div>
      <h2 className="text-2xl font-bold mb-2">Pair x Hour Heatmap</h2>
      <p className="text-sm text-gray-500 mb-6">
        Win rate by pair and hour of day (UTC). Green = profitable, red = unprofitable. Hover for details.
      </p>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr>
              <th className="text-left text-gray-500 px-2 py-1 w-20">Pair</th>
              {HOURS.map(h => (
                <th key={h} className="text-center text-gray-600 px-0.5 py-1 w-8">
                  {String(h).padStart(2, '0')}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pairs.map(pair => (
              <tr key={pair}>
                <td className="text-gray-300 px-2 py-1 font-medium whitespace-nowrap">
                  {pair.replace('_', '/')}
                </td>
                {HOURS.map(h => {
                  const cell = data[pair]?.[String(h)]
                  return (
                    <td key={h} className="px-0.5 py-0.5">
                      <div
                        className={`w-full h-7 rounded-sm ${getColor(cell)} flex items-center justify-center cursor-default group relative`}
                        title={cell ? `${cell.trades} trades, ${cell.win_rate}% win, £${cell.net_pl}` : 'No trades'}
                      >
                        {cell && cell.trades > 0 && (
                          <span className="text-[10px] text-white/70">{cell.trades}</span>
                        )}
                      </div>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 mt-4 text-xs text-gray-500">
        <span>Legend:</span>
        <div className="flex items-center gap-1"><div className="w-4 h-3 bg-green-600/60 rounded-sm" /> &gt;70%</div>
        <div className="flex items-center gap-1"><div className="w-4 h-3 bg-green-600/30 rounded-sm" /> 55-70%</div>
        <div className="flex items-center gap-1"><div className="w-4 h-3 bg-gray-700 rounded-sm" /> 45-55%</div>
        <div className="flex items-center gap-1"><div className="w-4 h-3 bg-red-600/30 rounded-sm" /> 30-45%</div>
        <div className="flex items-center gap-1"><div className="w-4 h-3 bg-red-600/60 rounded-sm" /> &lt;30%</div>
      </div>
    </div>
  )
}
