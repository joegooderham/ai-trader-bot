import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import PLBadge from '../components/PLBadge'
import StatCard from '../components/StatCard'

export default function WhatIf() {
  const { data: status } = useApi('/api/cmd/status')

  // Form state — initialise from current live config
  const [days, setDays] = useState(7)
  const [minConf, setMinConf] = useState('')
  const [disableSell, setDisableSell] = useState(false)
  const [disableBuy, setDisableBuy] = useState(false)
  const [disabledPairs, setDisabledPairs] = useState([])
  const [overnightThreshold, setOvernightThreshold] = useState('')

  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const allPairs = status?.pairs || []

  async function runSimulation() {
    setLoading(true)
    setError(null)
    try {
      const body = { days }
      if (minConf !== '') body.min_confidence = parseFloat(minConf)
      const dirs = []
      if (disableSell) dirs.push('SELL')
      if (disableBuy) dirs.push('BUY')
      if (dirs.length > 0) body.disabled_directions = dirs
      if (disabledPairs.length > 0) body.disabled_pairs = disabledPairs
      if (overnightThreshold !== '') body.hold_overnight_threshold = parseFloat(overnightThreshold)

      const resp = await fetch('/api/analysis/what-if', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      setResult(await resp.json())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  function togglePair(pair) {
    setDisabledPairs(prev =>
      prev.includes(pair) ? prev.filter(p => p !== pair) : [...prev, pair]
    )
  }

  // Use current config as presets
  function useCurrentSettings() {
    if (status) {
      setMinConf(status.min_confidence || '')
      setOvernightThreshold(status.hold_overnight_threshold || '')
      setDisableSell(status.disabled_directions?.includes('SELL') || false)
      setDisableBuy(status.disabled_directions?.includes('BUY') || false)
      setDisabledPairs(status.disabled_pairs || [])
    }
  }

  return (
    <div>
      <h2 className="text-2xl font-bold mb-2">Mystic Wolf</h2>
      <p className="text-sm text-gray-500 mb-6">
        Replay historical trades with different settings. See which trades would have been filtered and how P&L changes.
      </p>

      {/* Settings form */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 mb-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-medium text-gray-400 uppercase tracking-wide">Hypothetical Settings</h3>
          <button
            onClick={useCurrentSettings}
            className="text-xs text-blue-400 hover:text-blue-300"
          >
            Load current config
          </button>
        </div>

        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4 mb-4">
          {/* Time period */}
          <div>
            <label className="text-xs text-gray-500 block mb-1">Look-back period</label>
            <select
              value={days}
              onChange={e => setDays(parseInt(e.target.value))}
              className="w-full bg-gray-800 border border-gray-700 text-sm text-white rounded px-3 py-2"
            >
              <option value={3}>Last 3 days</option>
              <option value={7}>Last 7 days (this week)</option>
              <option value={14}>Last 14 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
              <option value={365}>All time</option>
            </select>
          </div>

          {/* Min confidence */}
          <div>
            <label className="text-xs text-gray-500 block mb-1">
              Min confidence % {status && <span className="text-gray-600">(current: {status.min_confidence}%)</span>}
            </label>
            <input
              type="number"
              value={minConf}
              onChange={e => setMinConf(e.target.value)}
              placeholder="e.g. 70"
              min={0} max={100} step={5}
              className="w-full bg-gray-800 border border-gray-700 text-sm text-white rounded px-3 py-2"
            />
          </div>

          {/* Overnight threshold */}
          <div>
            <label className="text-xs text-gray-500 block mb-1">
              Overnight hold % {status && <span className="text-gray-600">(current: {status.hold_overnight_threshold}%)</span>}
            </label>
            <input
              type="number"
              value={overnightThreshold}
              onChange={e => setOvernightThreshold(e.target.value)}
              placeholder="e.g. 65"
              min={0} max={100} step={5}
              className="w-full bg-gray-800 border border-gray-700 text-sm text-white rounded px-3 py-2"
            />
          </div>
        </div>

        {/* Direction toggles */}
        <div className="flex flex-wrap gap-3 mb-4">
          <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
            <input type="checkbox" checked={disableSell} onChange={e => setDisableSell(e.target.checked)}
              className="accent-red-500" />
            Disable SELL
          </label>
          <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
            <input type="checkbox" checked={disableBuy} onChange={e => setDisableBuy(e.target.checked)}
              className="accent-red-500" />
            Disable BUY
          </label>
        </div>

        {/* Pair toggles */}
        {allPairs.length > 0 && (
          <div className="mb-4">
            <label className="text-xs text-gray-500 block mb-2">Disable pairs:</label>
            <div className="flex flex-wrap gap-2">
              {allPairs.map(pair => (
                <button
                  key={pair}
                  onClick={() => togglePair(pair)}
                  className={`px-2 py-1 text-xs rounded border transition-colors ${
                    disabledPairs.includes(pair)
                      ? 'bg-red-600/20 text-red-400 border-red-600/30'
                      : 'bg-gray-800 text-gray-400 border-gray-700 hover:border-gray-600'
                  }`}
                >
                  {pair.replace('_', '/')}
                </button>
              ))}
            </div>
          </div>
        )}

        <button
          onClick={runSimulation}
          disabled={loading}
          className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 text-white text-sm font-medium rounded transition-colors"
        >
          {loading ? 'Simulating...' : 'Run Simulation'}
        </button>
      </div>

      {error && (
        <div className="bg-red-900/20 border border-red-800 rounded-lg p-3 mb-4 text-red-400 text-sm">
          Simulation failed: {error}
        </div>
      )}

      {/* Results */}
      {result && <SimulationResults result={result} />}
    </div>
  )
}

function SimulationResults({ result }) {
  const { actual, simulated, improvement, filtered_out, filter_reasons, filtered_by_pair } = result
  const plDiff = improvement.pl_difference
  const improved = plDiff > 0

  return (
    <div>
      {/* Headline comparison */}
      <div className="grid md:grid-cols-3 gap-4 mb-6">
        <StatCard
          label="Actual P&L"
          value={<PLBadge value={actual.pl} />}
          sub={`${actual.trades} trades, ${actual.win_rate}% win rate`}
        />
        <StatCard
          label="Simulated P&L"
          value={<PLBadge value={simulated.pl} />}
          sub={`${simulated.trades} trades, ${simulated.win_rate}% win rate`}
          className={improved ? 'border-green-800' : 'border-red-800'}
        />
        <StatCard
          label="Difference"
          value={<span className={`text-2xl font-bold ${improved ? 'text-green-400' : 'text-red-400'}`}>
            {plDiff >= 0 ? '+' : ''}£{plDiff.toFixed(2)}
          </span>}
          sub={`${improvement.trades_avoided} trades would be filtered out`}
        />
      </div>

      {/* What changed */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 mb-6">
        <h3 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">Impact Breakdown</h3>

        <div className="grid md:grid-cols-2 gap-4 mb-4">
          <div>
            <p className="text-xs text-gray-500 mb-1">Trades avoided</p>
            <p className="text-white">{improvement.trades_avoided} trades
              ({improvement.avoided_wins} wins, {improvement.avoided_losses} losses)</p>
          </div>
          <div>
            <p className="text-xs text-gray-500 mb-1">P&L of avoided trades</p>
            <PLBadge value={improvement.avoided_pl} />
            <span className="text-xs text-gray-500 ml-2">
              {improvement.avoided_pl < 0 ? '(you dodge these losses)' : '(you miss these gains)'}
            </span>
          </div>
        </div>

        {/* Filter reasons */}
        {filter_reasons && Object.keys(filter_reasons).length > 0 && (
          <div className="mb-4">
            <p className="text-xs text-gray-500 mb-2">Why trades were filtered:</p>
            <div className="flex flex-wrap gap-2">
              {Object.entries(filter_reasons).sort((a, b) => b[1] - a[1]).map(([reason, count]) => (
                <span key={reason} className="text-xs bg-gray-800 text-gray-300 px-2 py-1 rounded">
                  {reason}: <span className="font-bold">{count}</span>
                </span>
              ))}
            </div>
          </div>
        )}

        {/* By pair */}
        {filtered_by_pair && Object.keys(filtered_by_pair).length > 0 && (
          <div>
            <p className="text-xs text-gray-500 mb-2">Avoided trades by pair:</p>
            <div className="flex flex-wrap gap-3">
              {Object.entries(filtered_by_pair).map(([pair, stats]) => (
                <div key={pair} className="text-xs bg-gray-800 px-3 py-2 rounded">
                  <span className="text-gray-300 font-medium">{pair.replace('_', '/')}</span>
                  <span className="text-gray-500 ml-2">{stats.trades} trades</span>
                  <span className="ml-2">
                    <PLBadge value={stats.pl} />
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Filtered trades list */}
      {filtered_out.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
          <h3 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">
            Filtered Out Trades ({filtered_out.length})
          </h3>
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {filtered_out.map(t => (
              <div key={t.id} className="flex items-center justify-between bg-gray-800/50 rounded px-3 py-2 text-sm">
                <div className="flex items-center gap-2">
                  <span className={`text-xs px-1.5 py-0.5 rounded ${
                    t.direction === 'BUY' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                  }`}>{t.direction}</span>
                  <span className="text-gray-300">{t.pair.replace('_', '/')}</span>
                  <span className="text-xs text-gray-500">{t.opened_at?.substring(0, 16)}</span>
                  <span className="text-xs text-gray-600">({t.confidence?.toFixed(0)}%)</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-yellow-400/70">{t.filter_reasons.join(', ')}</span>
                  <PLBadge value={t.pl} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
