import { useApi } from '../hooks/useApi'
import StatCard from '../components/StatCard'
import PLBadge from '../components/PLBadge'

export default function RiskExposure() {
  const { data, loading, error } = useApi('/api/analytics/risk-exposure', 30000)

  if (loading) return <div className="text-gray-500">Loading risk data...</div>
  if (error) return <div className="text-red-400">Failed: {error}</div>
  if (!data) return null

  return (
    <div>
      <h2 className="text-2xl font-bold mb-2">Risk Exposure</h2>
      <p className="text-sm text-gray-500 mb-6">
        Current portfolio risk, margin utilisation, and correlated exposure.
      </p>

      {/* Account overview */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <StatCard label="Balance" value={`£${(data.account_balance || 0).toFixed(2)}`} />
        <StatCard label="Available" value={`£${(data.available || 0).toFixed(2)}`} />
        <StatCard label="Unrealised P&L" value={<PLBadge value={data.unrealized_pl || 0} />} />
        <StatCard
          label="Total Risk"
          value={`£${(data.total_risk || 0).toFixed(2)}`}
          sub={`${data.risk_pct_of_capital || 0}% of capital`}
          className={data.risk_pct_of_capital > 10 ? 'border-red-800' : ''}
        />
      </div>

      {/* Per-position risk */}
      {data.position_risk?.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 mb-6">
          <h3 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">Risk Per Position</h3>
          <div className="space-y-2">
            {data.position_risk.map((pos, i) => (
              <div key={i} className="flex items-center justify-between bg-gray-800/50 rounded px-3 py-2">
                <div className="flex items-center gap-3">
                  <span className={`text-xs px-1.5 py-0.5 rounded ${
                    pos.direction === 'BUY' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                  }`}>{pos.direction}</span>
                  <span className="text-sm text-gray-300">{(pos.pair || '').replace('_', '/')}</span>
                  {pos.confidence && (
                    <span className="text-xs text-gray-500">{pos.confidence.toFixed(0)}%</span>
                  )}
                </div>
                <span className="text-sm font-mono text-red-400">£{pos.risk_amount.toFixed(2)} at risk</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {data.position_risk?.length === 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-8 text-center text-gray-500 mb-6">
          No open positions — zero risk exposure.
        </div>
      )}

      {/* Correlated exposure warnings */}
      {data.correlated_exposure?.length > 0 && (
        <div className="bg-gray-900 border border-yellow-800/50 rounded-lg p-5">
          <h3 className="text-sm font-medium text-yellow-400 uppercase tracking-wide mb-3">
            Correlated Exposure Warnings
          </h3>
          <div className="space-y-2">
            {data.correlated_exposure.map((c, i) => (
              <div key={i} className="flex items-center justify-between bg-yellow-900/10 rounded px-3 py-2">
                <span className="text-sm text-gray-300">
                  {c.pair_a.replace('_', '/')} + {c.pair_b.replace('_', '/')}
                </span>
                <div className="text-right">
                  <span className={`text-sm font-mono ${c.correlation > 0 ? 'text-yellow-400' : 'text-orange-400'}`}>
                    {c.correlation > 0 ? '+' : ''}{c.correlation.toFixed(2)}
                  </span>
                  <span className="text-xs text-gray-500 ml-2">{c.warning}</span>
                </div>
              </div>
            ))}
          </div>
          <p className="text-xs text-gray-600 mt-3">
            Highly correlated positions double your directional exposure. Inverse positions hedge each other.
          </p>
        </div>
      )}
    </div>
  )
}
