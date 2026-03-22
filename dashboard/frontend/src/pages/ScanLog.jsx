import { useState } from 'react'
import { useApi } from '../hooks/useApi'

export default function ScanLog() {
  const [pair, setPair] = useState('')
  const [tradedOnly, setTradedOnly] = useState(false)

  const url = `/api/scan-log?limit=100${pair ? `&pair=${pair}` : ''}${tradedOnly ? '&traded_only=true' : ''}`
  const { data, loading } = useApi(url, 30000)

  const scans = data?.scans || []
  const allPairs = [...new Set(scans.map(s => s.pair))].sort()

  return (
    <div>
      <h2 className="text-2xl font-bold mb-2">Scan Audit Log</h2>
      <p className="text-sm text-gray-500 mb-4">
        Every pair evaluation — see why the bot did or didn't trade at any point.
      </p>

      {/* Filters */}
      <div className="flex flex-wrap gap-2 mb-4">
        <select
          value={pair}
          onChange={e => setPair(e.target.value)}
          className="bg-gray-900 border border-gray-700 text-sm text-gray-300 rounded px-3 py-1.5"
        >
          <option value="">All Pairs</option>
          {allPairs.map(p => <option key={p} value={p}>{p.replace('_', '/')}</option>)}
        </select>
        <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
          <input type="checkbox" checked={tradedOnly} onChange={e => setTradedOnly(e.target.checked)}
            className="accent-blue-500" />
          Traded only
        </label>
        <span className="text-xs text-gray-500 self-center ml-2">{scans.length} scans</span>
      </div>

      {loading && scans.length === 0 && <div className="text-gray-500">Loading...</div>}

      <div className="space-y-2 max-h-[70vh] overflow-y-auto">
        {scans.map(scan => (
          <ScanEntry key={scan.id} scan={scan} />
        ))}
      </div>

      {scans.length === 0 && !loading && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-8 text-center text-gray-500">
          No scan data yet. The log will populate when markets are open and the bot is scanning.
        </div>
      )}
    </div>
  )
}

function ScanEntry({ scan }) {
  const [expanded, setExpanded] = useState(false)
  const traded = scan.traded === 1
  const pair = (scan.pair || '').replace('_', '/')
  const time = (scan.timestamp || '').substring(11, 19)
  const date = (scan.timestamp || '').substring(0, 10)

  return (
    <div
      className={`bg-gray-900 border rounded-lg cursor-pointer transition-colors ${
        traded ? 'border-green-900/50 hover:border-green-800/60' : 'border-gray-800 hover:border-gray-700'
      }`}
      onClick={() => setExpanded(!expanded)}
    >
      {/* Header row */}
      <div className="flex items-center justify-between px-3 py-2">
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${traded ? 'bg-green-500' : 'bg-gray-600'}`} />
          <span className="text-xs text-gray-500">{date} {time}</span>
          <span className="text-sm text-gray-300 font-medium">{pair}</span>
          {scan.direction && (
            <span className={`text-xs px-1.5 py-0.5 rounded ${
              scan.direction === 'BUY' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
            }`}>{scan.direction}</span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm font-mono text-gray-400">
            {scan.confidence_score?.toFixed(1)}%
          </span>
          {traded ? (
            <span className="text-xs text-green-400">TRADED</span>
          ) : (
            <span className="text-xs text-gray-600">{scan.skip_reason || 'skipped'}</span>
          )}
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-gray-800 px-3 py-3 text-sm" onClick={e => e.stopPropagation()}>
          {/* Breakdown */}
          {scan.breakdown && (
            <div className="mb-3">
              <p className="text-xs text-gray-500 uppercase mb-1">Score Breakdown</p>
              <div className="flex flex-wrap gap-2">
                {Object.entries(scan.breakdown).map(([key, val]) => (
                  <span key={key} className="text-xs bg-gray-800 px-2 py-1 rounded">
                    <span className="text-gray-500">{key}:</span>{' '}
                    <span className="text-white font-mono">{typeof val === 'number' ? val.toFixed(1) : val}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Indicators */}
          {scan.indicators && (
            <div className="mb-3">
              <p className="text-xs text-gray-500 uppercase mb-1">Indicators</p>
              <div className="flex flex-wrap gap-2">
                {Object.entries(scan.indicators).map(([key, val]) => (
                  <span key={key} className="text-xs bg-gray-800 px-2 py-1 rounded">
                    <span className="text-gray-500">{key}:</span>{' '}
                    <span className="text-white font-mono">{typeof val === 'number' ? val.toFixed(4) : String(val)}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* LSTM */}
          {scan.lstm_prediction && (
            <div className="mb-3">
              <p className="text-xs text-gray-500 uppercase mb-1">LSTM Prediction</p>
              <span className="text-xs bg-gray-800 px-2 py-1 rounded text-white">
                {scan.lstm_prediction.direction} ({(scan.lstm_prediction.probability * 100).toFixed(0)}%)
              </span>
            </div>
          )}

          {/* Reasoning */}
          {scan.reasoning && (
            <div>
              <p className="text-xs text-gray-500 uppercase mb-1">Reasoning</p>
              <p className="text-xs text-gray-400 leading-relaxed">{scan.reasoning}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
