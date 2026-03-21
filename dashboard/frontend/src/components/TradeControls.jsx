import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import { useCommand } from '../hooks/useCommand'
import ConfirmModal from './ConfirmModal'

/**
 * Trade control bar — pause/resume, close all, close profitable, close losing.
 * Fetches bot status to show paused/active state and disabled directions/pairs.
 */
export default function TradeControls({ onAction }) {
  const { data: status, loading } = useApi('/api/cmd/status', 10000)
  const { execute, loading: cmdLoading } = useCommand()
  const [confirm, setConfirm] = useState(null)

  const paused = status?.paused
  const disabledDirs = status?.disabled_directions || []
  const disabledPairs = status?.disabled_pairs || []

  async function handleAction(action, body = null) {
    try {
      const result = await execute(action, body)
      onAction?.(action, result)
    } catch {
      onAction?.(action, null, true)
    }
    setConfirm(null)
  }

  if (loading && !status) return null

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 mb-4 p-3 bg-gray-900 border border-gray-800 rounded-lg">
        {/* Pause/Resume toggle */}
        <button
          onClick={() => handleAction(paused ? 'resume' : 'pause')}
          disabled={cmdLoading}
          className={`px-3 py-1.5 text-xs font-medium rounded transition-colors ${
            paused
              ? 'bg-green-600/20 text-green-400 hover:bg-green-600/30 border border-green-600/30'
              : 'bg-yellow-600/20 text-yellow-400 hover:bg-yellow-600/30 border border-yellow-600/30'
          }`}
        >
          {paused ? '▶ Resume Trading' : '⏸ Pause Trading'}
        </button>

        <div className="w-px h-6 bg-gray-700" />

        {/* Close buttons */}
        <button
          onClick={() => setConfirm({ action: 'close-all', title: 'Close All Positions', message: 'This will close every open position immediately. Are you sure?', danger: true })}
          disabled={cmdLoading}
          className="px-3 py-1.5 text-xs text-red-400 bg-red-600/10 hover:bg-red-600/20 border border-red-600/20 rounded transition-colors"
        >
          Close All
        </button>
        <button
          onClick={() => setConfirm({ action: 'close-profitable', title: 'Close Profitable', message: 'Close all positions currently in profit?', danger: false })}
          disabled={cmdLoading}
          className="px-3 py-1.5 text-xs text-green-400 bg-green-600/10 hover:bg-green-600/20 border border-green-600/20 rounded transition-colors"
        >
          Close Profitable
        </button>
        <button
          onClick={() => setConfirm({ action: 'close-losing', title: 'Close Losing', message: 'Close all positions currently at a loss?', danger: true })}
          disabled={cmdLoading}
          className="px-3 py-1.5 text-xs text-red-400 bg-red-600/10 hover:bg-red-600/20 border border-red-600/20 rounded transition-colors"
        >
          Close Losing
        </button>

        {/* Status indicator */}
        <div className="ml-auto flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${paused ? 'bg-yellow-500' : 'bg-green-500'} animate-pulse`} />
          <span className="text-xs text-gray-400">{paused ? 'Paused' : 'Active'}</span>
        </div>
      </div>

      {/* Disabled directions/pairs badges */}
      {(disabledDirs.length > 0 || disabledPairs.length > 0) && (
        <div className="flex flex-wrap gap-2 mb-4">
          {disabledDirs.map(d => (
            <button
              key={d}
              onClick={() => handleAction('enable-direction', { direction: d })}
              className="flex items-center gap-1 px-2 py-1 text-xs bg-red-600/10 text-red-400 border border-red-600/20 rounded hover:bg-red-600/20 transition-colors"
            >
              <span>{d} disabled</span>
              <span className="text-red-300">✕</span>
            </button>
          ))}
          {disabledPairs.map(p => (
            <button
              key={p}
              onClick={() => handleAction('enable-pair', { pair: p })}
              className="flex items-center gap-1 px-2 py-1 text-xs bg-orange-600/10 text-orange-400 border border-orange-600/20 rounded hover:bg-orange-600/20 transition-colors"
            >
              <span>{p.replace('_', '/')} disabled</span>
              <span className="text-orange-300">✕</span>
            </button>
          ))}
        </div>
      )}

      {/* Confirm modal */}
      {confirm && (
        <ConfirmModal
          title={confirm.title}
          message={confirm.message}
          confirmLabel={confirm.title}
          danger={confirm.danger}
          onConfirm={() => handleAction(confirm.action)}
          onCancel={() => setConfirm(null)}
        />
      )}
    </>
  )
}
