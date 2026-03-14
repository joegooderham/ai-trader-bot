import { useApi } from '../hooks/useApi'
import PLBadge from '../components/PLBadge'

export default function Positions() {
  // Refresh every 15 seconds for near-real-time position updates
  const { data, loading, error } = useApi('/api/positions', 15000)

  if (loading) return <p className="text-gray-400">Loading positions...</p>
  if (error) return <p className="text-red-400">Error: {error}</p>

  const positions = data?.positions || []

  return (
    <div>
      <div className="flex items-center justify-between mb-4 md:mb-6">
        <h2 className="text-xl md:text-2xl font-bold">Open Positions</h2>
        <span className="text-sm text-gray-500">{positions.length} active</span>
      </div>

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
    <div className={`bg-gray-900 border rounded-lg p-4 md:p-5 ${
      isBuy ? 'border-green-900/50' : 'border-red-900/50'
    }`}>
      {/* Header: pair + direction badge */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <span className="text-lg md:text-xl font-bold text-white">
            {position.pair?.replace('_', '/')}
          </span>
          <span className={`px-2.5 py-1 rounded text-xs font-bold uppercase tracking-wide ${
            isBuy
              ? 'bg-green-500/20 text-green-400 border border-green-500/30'
              : 'bg-red-500/20 text-red-400 border border-red-500/30'
          }`}>
            {position.direction}
          </span>
        </div>
        <span className="text-xs text-gray-500">
          {position.status}
        </span>
      </div>

      {/* Price details grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
        <Detail label="Entry Price" value={position.fill_price} mono />
        <Detail label="Size" value={`${position.size} lot${position.size !== 1 ? 's' : ''}`} />
        <Detail
          label="Stop Loss"
          value={position.stop_loss}
          mono
          className="text-red-400"
        />
        <Detail
          label="Take Profit"
          value={position.take_profit}
          mono
          className="text-green-400"
        />
      </div>

      {/* Confidence + opened time */}
      <div className="flex flex-wrap items-center justify-between mt-3 pt-3 border-t border-gray-800/50">
        {position.confidence_score ? (
          <span className="text-sm text-gray-400">
            Confidence: <span className="text-white font-medium">{position.confidence_score}%</span>
          </span>
        ) : (
          <span className="text-xs text-gray-600">No confidence data</span>
        )}
        <span className="text-xs text-gray-500">
          Opened: {position.opened_at?.slice(0, 16).replace('T', ' ')}
        </span>
      </div>
    </div>
  )
}

function Detail({ label, value, mono = false, className = 'text-white' }) {
  return (
    <div>
      <p className="text-xs text-gray-500 mb-0.5">{label}</p>
      <p className={`text-sm font-medium ${mono ? 'font-mono' : ''} ${className}`}>
        {value ?? '\u2014'}
      </p>
    </div>
  )
}
