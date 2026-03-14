import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import PLBadge from '../components/PLBadge'

const PAIRS = ['All', 'EUR_USD', 'GBP_USD', 'USD_JPY', 'AUD_USD']

export default function TradeHistory() {
  const [selectedPair, setSelectedPair] = useState('All')
  const [page, setPage] = useState(0)
  const limit = 20

  const pairParam = selectedPair === 'All' ? '' : `&pair=${selectedPair}`
  const { data, loading, error } = useApi(
    `/api/trades?limit=${limit}&offset=${page * limit}${pairParam}`
  )

  if (error) return <p className="text-red-400">Error: {error}</p>

  const trades = data?.trades || []
  const total = data?.total || 0
  const totalPages = Math.ceil(total / limit)

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">Trade History</h2>
        <p className="text-sm text-gray-500">{total} total trades</p>
      </div>

      {/* Pair filter */}
      <div className="flex gap-2 mb-4">
        {PAIRS.map(pair => (
          <button
            key={pair}
            onClick={() => { setSelectedPair(pair); setPage(0) }}
            className={`px-3 py-1.5 rounded text-sm transition-colors ${
              selectedPair === pair
                ? 'bg-blue-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:text-white'
            }`}
          >
            {pair === 'All' ? 'All Pairs' : pair.replace('_', '/')}
          </button>
        ))}
      </div>

      {/* Trades table */}
      {loading ? (
        <p className="text-gray-400">Loading...</p>
      ) : trades.length === 0 ? (
        <p className="text-gray-500">No closed trades found</p>
      ) : (
        <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-500 text-left bg-gray-800/50">
                <th className="px-4 py-3">Pair</th>
                <th className="px-4 py-3">Direction</th>
                <th className="px-4 py-3">Entry</th>
                <th className="px-4 py-3">Exit</th>
                <th className="px-4 py-3">P&L</th>
                <th className="px-4 py-3">Reason</th>
                <th className="px-4 py-3">Closed</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((trade, i) => (
                <tr key={i} className="border-t border-gray-800/50 hover:bg-gray-800/30">
                  <td className="px-4 py-3 font-medium text-white">
                    {trade.pair?.replace('_', '/')}
                  </td>
                  <td className={`px-4 py-3 ${
                    trade.direction === 'BUY' ? 'text-profit' : 'text-loss'
                  }`}>
                    {trade.direction}
                  </td>
                  <td className="px-4 py-3 font-mono">{trade.entry_price}</td>
                  <td className="px-4 py-3 font-mono">{trade.close_price}</td>
                  <td className="px-4 py-3">
                    <PLBadge value={trade.profit_loss || 0} />
                  </td>
                  <td className="px-4 py-3 text-gray-400 max-w-[200px] truncate">
                    {trade.close_reason || '—'}
                  </td>
                  <td className="px-4 py-3 text-gray-400">
                    {trade.closed_at?.slice(0, 16)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-4 mt-4">
          <button
            onClick={() => setPage(p => Math.max(0, p - 1))}
            disabled={page === 0}
            className="px-3 py-1.5 rounded bg-gray-800 text-gray-400 disabled:opacity-30 hover:text-white"
          >
            Previous
          </button>
          <span className="text-sm text-gray-500">
            Page {page + 1} of {totalPages}
          </span>
          <button
            onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
            className="px-3 py-1.5 rounded bg-gray-800 text-gray-400 disabled:opacity-30 hover:text-white"
          >
            Next
          </button>
        </div>
      )}
    </div>
  )
}
