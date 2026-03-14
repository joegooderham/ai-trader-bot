import { useApi } from '../hooks/useApi'

export default function Positions() {
  // Refresh every 15 seconds for near-real-time position updates
  const { data, loading, error } = useApi('/api/positions', 15000)

  if (loading) return <p className="text-gray-400">Loading positions...</p>
  if (error) return <p className="text-red-400">Error: {error}</p>

  const positions = data?.positions || []

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">Open Positions</h2>

      {positions.length === 0 ? (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-8 text-center">
          <p className="text-gray-500 text-lg">No open positions</p>
          <p className="text-gray-600 text-sm mt-2">The bot will open trades when confidence thresholds are met</p>
        </div>
      ) : (
        <div className="grid gap-4">
          {positions.map((pos, i) => (
            <PositionCard key={i} position={pos} />
          ))}
        </div>
      )}
    </div>
  )
}

function PositionCard({ position }) {
  const isBuy = position.direction === 'BUY'

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <span className="text-xl font-bold text-white">
            {position.pair?.replace('_', '/')}
          </span>
          <span className={`px-2 py-0.5 rounded text-xs font-medium ${
            isBuy ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'
          }`}>
            {position.direction}
          </span>
        </div>
        {position.confidence_score && (
          <span className="text-sm text-gray-400">
            Confidence: <span className="text-white font-medium">{position.confidence_score}%</span>
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <div>
          <p className="text-gray-500">Entry Price</p>
          <p className="font-mono text-white">{position.entry_price}</p>
        </div>
        <div>
          <p className="text-gray-500">Units</p>
          <p className="text-white">{position.units?.toLocaleString()}</p>
        </div>
        <div>
          <p className="text-gray-500">Stop Loss</p>
          <p className="font-mono text-red-400">{position.stop_loss || '—'}</p>
        </div>
        <div>
          <p className="text-gray-500">Take Profit</p>
          <p className="font-mono text-green-400">{position.take_profit || '—'}</p>
        </div>
      </div>

      {position.opened_at && (
        <p className="text-xs text-gray-600 mt-3">
          Opened: {position.opened_at}
        </p>
      )}
    </div>
  )
}
