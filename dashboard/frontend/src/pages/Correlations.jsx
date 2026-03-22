import { useApi } from '../hooks/useApi'

export default function Correlations() {
  const { data, loading, error } = useApi('/api/analytics/correlations')

  if (loading) return <div className="text-gray-500">Loading correlations...</div>
  if (error) return <div className="text-red-400">Failed: {error}</div>
  if (!data) return null

  const pairs = Object.keys(data)

  function getColor(val) {
    if (val === 1) return 'bg-blue-600'
    if (val >= 0.8) return 'bg-green-600/80'
    if (val >= 0.6) return 'bg-green-600/50'
    if (val >= 0.3) return 'bg-green-600/20'
    if (val > -0.3) return 'bg-gray-800'
    if (val > -0.6) return 'bg-red-600/20'
    if (val > -0.8) return 'bg-red-600/50'
    return 'bg-red-600/80'
  }

  return (
    <div>
      <h2 className="text-2xl font-bold mb-2">Pair Correlations</h2>
      <p className="text-sm text-gray-500 mb-6">
        Green = move together (correlated). Red = move opposite (inverse). Hover for values.
      </p>

      <div className="overflow-x-auto">
        <table className="text-xs">
          <thead>
            <tr>
              <th className="w-16" />
              {pairs.map(p => (
                <th key={p} className="text-gray-500 px-1 py-2 w-12 text-center">
                  <span className="inline-block -rotate-45 origin-center whitespace-nowrap">
                    {p.replace('_', '/').substring(0, 7)}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pairs.map(p1 => (
              <tr key={p1}>
                <td className="text-gray-300 pr-2 py-1 font-medium whitespace-nowrap text-right">
                  {p1.replace('_', '/')}
                </td>
                {pairs.map(p2 => {
                  const val = data[p1]?.[p2] ?? 0
                  return (
                    <td key={p2} className="px-0.5 py-0.5">
                      <div
                        className={`w-10 h-8 rounded-sm ${getColor(val)} flex items-center justify-center cursor-default`}
                        title={`${p1.replace('_','/')} vs ${p2.replace('_','/')}: ${val.toFixed(2)}`}
                      >
                        {val !== 0 && (
                          <span className={`text-[10px] font-mono ${Math.abs(val) >= 0.6 ? 'text-white' : 'text-gray-400'}`}>
                            {val > 0 ? '+' : ''}{val.toFixed(1)}
                          </span>
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

      <div className="flex items-center gap-4 mt-4 text-xs text-gray-500">
        <span>Legend:</span>
        <div className="flex items-center gap-1"><div className="w-4 h-3 bg-green-600/80 rounded-sm" /> Strong +ve</div>
        <div className="flex items-center gap-1"><div className="w-4 h-3 bg-green-600/30 rounded-sm" /> Weak +ve</div>
        <div className="flex items-center gap-1"><div className="w-4 h-3 bg-gray-800 rounded-sm" /> Neutral</div>
        <div className="flex items-center gap-1"><div className="w-4 h-3 bg-red-600/30 rounded-sm" /> Weak -ve</div>
        <div className="flex items-center gap-1"><div className="w-4 h-3 bg-red-600/80 rounded-sm" /> Strong -ve</div>
      </div>
    </div>
  )
}
