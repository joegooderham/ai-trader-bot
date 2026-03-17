import { useApi } from '../hooks/useApi'

/**
 * Running P&L display for the top-right corner of the layout.
 * Polls /api/running-pl every 30 seconds to keep the total current.
 */
export default function RunningPL() {
  // Refresh every 30s so the number stays reasonably live
  const { data } = useApi('/api/running-pl', 30000)

  if (!data) return null

  const pl = data.total_pl || 0
  const isPositive = pl >= 0
  const formatted = `${isPositive ? '+' : ''}£${pl.toFixed(2)}`

  // Format timestamp as "HH:MM:SS" for compactness
  let updatedStr = ''
  if (data.updated_at) {
    const d = new Date(data.updated_at)
    updatedStr = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }

  return (
    <div className="text-right">
      <span className={`text-lg font-bold font-mono ${isPositive ? 'text-profit' : 'text-loss'}`}>
        {formatted}
      </span>
      {updatedStr && (
        <p className="text-[10px] text-gray-500 mt-0.5">
          Updated {updatedStr}
        </p>
      )}
    </div>
  )
}
