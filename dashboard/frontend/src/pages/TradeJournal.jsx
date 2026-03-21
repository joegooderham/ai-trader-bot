import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import PLBadge from '../components/PLBadge'

export default function TradeJournal() {
  const [filter, setFilter] = useState({ pair: '', direction: '', outcome: '' })
  const { data, loading, error } = useApi('/api/trades?limit=50')

  const trades = (data?.trades || []).filter(t => {
    if (filter.pair && t.pair !== filter.pair) return false
    if (filter.direction && t.direction !== filter.direction) return false
    if (filter.outcome === 'win' && (t.pl || 0) <= 0) return false
    if (filter.outcome === 'loss' && (t.pl || 0) >= 0) return false
    return true
  })

  // Get unique pairs for filter
  const allPairs = [...new Set((data?.trades || []).map(t => t.pair))].sort()

  return (
    <div>
      <h2 className="text-2xl font-bold mb-2">Trade Journal</h2>
      <p className="text-sm text-gray-500 mb-4">Click a trade to see full details.</p>

      {/* Filters */}
      <div className="flex flex-wrap gap-2 mb-4">
        <select
          value={filter.pair}
          onChange={e => setFilter(prev => ({ ...prev, pair: e.target.value }))}
          className="bg-gray-900 border border-gray-700 text-sm text-gray-300 rounded px-3 py-1.5"
        >
          <option value="">All Pairs</option>
          {allPairs.map(p => <option key={p} value={p}>{p.replace('_', '/')}</option>)}
        </select>
        <select
          value={filter.direction}
          onChange={e => setFilter(prev => ({ ...prev, direction: e.target.value }))}
          className="bg-gray-900 border border-gray-700 text-sm text-gray-300 rounded px-3 py-1.5"
        >
          <option value="">All Directions</option>
          <option value="BUY">BUY</option>
          <option value="SELL">SELL</option>
        </select>
        <select
          value={filter.outcome}
          onChange={e => setFilter(prev => ({ ...prev, outcome: e.target.value }))}
          className="bg-gray-900 border border-gray-700 text-sm text-gray-300 rounded px-3 py-1.5"
        >
          <option value="">All Outcomes</option>
          <option value="win">Wins</option>
          <option value="loss">Losses</option>
        </select>
        <span className="text-xs text-gray-500 self-center ml-2">{trades.length} trades</span>
      </div>

      {loading && <div className="text-gray-500">Loading...</div>}
      {error && <div className="text-red-400">Failed to load trades</div>}

      <div className="space-y-2">
        {trades.map(trade => (
          <TradeCard key={trade.id} trade={trade} />
        ))}
      </div>
    </div>
  )
}

function TradeCard({ trade }) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState(null)

  async function toggleExpand() {
    if (!expanded && !detail) {
      try {
        const resp = await fetch(`/api/trades/${trade.id}/detail`)
        if (resp.ok) setDetail(await resp.json())
      } catch { /* ignore */ }
    }
    setExpanded(!expanded)
  }

  const pl = trade.pl || 0
  const isWin = pl > 0
  const direction = trade.direction || '?'
  const pair = (trade.pair || '').replace('_', '/')
  const date = (trade.opened_at || '').substring(0, 16).replace('T', ' ')

  // Calculate duration
  let duration = ''
  if (trade.opened_at && trade.closed_at) {
    const ms = new Date(trade.closed_at) - new Date(trade.opened_at)
    const mins = Math.floor(ms / 60000)
    if (mins < 60) duration = `${mins}m`
    else duration = `${Math.floor(mins / 60)}h ${mins % 60}m`
  }

  // R:R achieved
  let rr = ''
  if (trade.fill_price && trade.stop_loss && trade.close_price) {
    const risk = Math.abs(trade.fill_price - trade.stop_loss)
    const reward = Math.abs(trade.close_price - trade.fill_price)
    if (risk > 0) rr = `${(reward / risk).toFixed(1)}:1`
  }

  return (
    <div
      className={`bg-gray-900 border rounded-lg transition-colors cursor-pointer ${
        isWin ? 'border-green-900/40 hover:border-green-800/60' : 'border-red-900/40 hover:border-red-800/60'
      }`}
      onClick={toggleExpand}
    >
      {/* Collapsed header */}
      <div className="flex items-center justify-between p-4">
        <div className="flex items-center gap-3">
          <span className={`text-xs px-2 py-0.5 rounded ${
            direction === 'BUY' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
          }`}>{direction}</span>
          <span className="text-white font-medium">{pair}</span>
          <span className="text-xs text-gray-500">{date}</span>
          {duration && <span className="text-xs text-gray-600">{duration}</span>}
          {rr && <span className="text-xs text-gray-600">R:R {rr}</span>}
        </div>
        <div className="flex items-center gap-3">
          <PLBadge value={pl} />
          <svg className={`w-4 h-4 text-gray-500 transition-transform ${expanded ? 'rotate-180' : ''}`}
            fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-gray-800 p-4 text-sm" onClick={e => e.stopPropagation()}>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
            <Detail label="Entry" value={trade.fill_price} mono />
            <Detail label="Exit" value={trade.close_price || 'Open'} mono />
            <Detail label="Stop Loss" value={trade.stop_loss} mono />
            <Detail label="Take Profit" value={trade.take_profit} mono />
            <Detail label="Confidence" value={`${trade.confidence_score?.toFixed(0) || '?'}%`} />
            <Detail label="Size" value={trade.size} mono />
            <Detail label="Close Reason" value={trade.close_reason || '-'} />
            <Detail label="Status" value={trade.status || '-'} />
          </div>

          {/* Confidence breakdown */}
          {detail?.breakdown && typeof detail.breakdown === 'object' && (
            <div className="mb-4">
              <p className="text-xs text-gray-500 uppercase mb-2">Confidence Breakdown</p>
              <div className="flex flex-wrap gap-3">
                {Object.entries(detail.breakdown).map(([key, val]) => (
                  <span key={key} className="text-xs bg-gray-800 px-2 py-1 rounded">
                    <span className="text-gray-500">{key}:</span>{' '}
                    <span className="text-white font-mono">{typeof val === 'number' ? val.toFixed(1) : val}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Reasoning */}
          {(detail?.reasoning || trade.reasoning) && (
            <div>
              <p className="text-xs text-gray-500 uppercase mb-1">Reasoning</p>
              <p className="text-gray-400 text-xs leading-relaxed">
                {(detail?.reasoning || trade.reasoning || '').substring(0, 500)}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Detail({ label, value, mono }) {
  return (
    <div>
      <p className="text-xs text-gray-500">{label}</p>
      <p className={`text-sm ${mono ? 'font-mono' : ''} text-gray-200`}>{value ?? '-'}</p>
    </div>
  )
}
