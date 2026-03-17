import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import PLBadge from '../components/PLBadge'

const PAIRS = [
  'All', 'EUR_USD', 'GBP_USD', 'USD_JPY', 'AUD_USD', 'USD_CAD',
  'USD_CHF', 'GBP_JPY', 'EUR_GBP', 'EUR_JPY', 'NZD_USD',
]

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
      <div className="flex items-center justify-between mb-4 md:mb-6">
        <h2 className="text-xl md:text-2xl font-bold">Trade History</h2>
        <p className="text-sm text-gray-500">{total} total</p>
      </div>

      {/* Pair filter */}
      <div className="flex flex-wrap gap-2 mb-4">
        {PAIRS.map(pair => (
          <button
            key={pair}
            onClick={() => { setSelectedPair(pair); setPage(0) }}
            className={`px-3 py-1.5 rounded text-xs md:text-sm transition-colors ${
              selectedPair === pair
                ? 'bg-blue-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:text-white'
            }`}
          >
            {pair === 'All' ? 'All' : pair.replace('_', '/')}
          </button>
        ))}
      </div>

      {loading ? (
        <p className="text-gray-400">Loading...</p>
      ) : trades.length === 0 ? (
        <p className="text-gray-500">No closed trades found</p>
      ) : (
        <>
          {/* Desktop table */}
          <div className="hidden md:block bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
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
                    <td className="px-4 py-3 font-mono">{trade.fill_price}</td>
                    <td className="px-4 py-3 font-mono">{trade.close_price}</td>
                    <td className="px-4 py-3">
                      <PLBadge value={trade.pl || 0} />
                    </td>
                    <td className="px-4 py-3 text-gray-400 max-w-[200px] truncate">
                      {trade.close_reason || '\u2014'}
                    </td>
                    <td className="px-4 py-3 text-gray-400">
                      {trade.closed_at?.slice(0, 16)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile cards */}
          <div className="md:hidden space-y-3">
            {trades.map((trade, i) => (
              <div key={i} className="bg-gray-900 border border-gray-800 rounded-lg p-3">
                <div className="flex justify-between items-center mb-2">
                  <span className="font-medium text-white">{trade.pair?.replace('_', '/')}</span>
                  <PLBadge value={trade.pl || 0} />
                </div>
                <div className="flex justify-between text-xs text-gray-400">
                  <span className={trade.direction === 'BUY' ? 'text-profit' : 'text-loss'}>
                    {trade.direction}
                  </span>
                  <span>{trade.closed_at?.slice(0, 10)}</span>
                </div>
                <div className="text-xs text-gray-500 mt-1">
                  {trade.fill_price} &rarr; {trade.close_price}
                  {trade.close_reason && ` | ${trade.close_reason}`}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-4 mt-4">
          <button
            onClick={() => setPage(p => Math.max(0, p - 1))}
            disabled={page === 0}
            className="px-3 py-1.5 rounded bg-gray-800 text-gray-400 disabled:opacity-30 hover:text-white text-sm"
          >
            Previous
          </button>
          <span className="text-sm text-gray-500">
            {page + 1} / {totalPages}
          </span>
          <button
            onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
            className="px-3 py-1.5 rounded bg-gray-800 text-gray-400 disabled:opacity-30 hover:text-white text-sm"
          >
            Next
          </button>
        </div>
      )}
    </div>
  )
}
