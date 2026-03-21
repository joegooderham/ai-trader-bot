import { useState, useEffect } from 'react'
import { useApi } from '../hooks/useApi'
import { useCommand } from '../hooks/useCommand'
import { useToast } from '../components/Toast'

const TYPE_BADGES = {
  disable_direction: { label: 'Direction', color: 'bg-red-600/20 text-red-400' },
  enable_direction: { label: 'Re-enable', color: 'bg-green-600/20 text-green-400' },
  remove_pair: { label: 'Remove Pair', color: 'bg-orange-600/20 text-orange-400' },
  runtime_config_change: { label: 'Config', color: 'bg-blue-600/20 text-blue-400' },
  pause_trading: { label: 'Pause', color: 'bg-yellow-600/20 text-yellow-400' },
  config_change: { label: 'Config (restart)', color: 'bg-gray-600/20 text-gray-400' },
}

export default function Remediation() {
  const { data, loading } = useApi('/api/cmd/remediation', 15000)
  const { execute, loading: cmdLoading } = useCommand()
  const { showToast, ToastComponent } = useToast()
  const [actions, setActions] = useState([])

  useEffect(() => {
    if (data?.pending_actions) {
      setActions(data.pending_actions)
    }
  }, [data])

  async function handleApprove(id) {
    try {
      const result = await execute(`remediation/${id}/approve`)
      setActions(prev => prev.filter(a => a.action_id !== id))
      showToast(result?.result?.split('\n')?.[0] || `Action #${id} approved`)
    } catch (err) {
      showToast(`Failed: ${err.message}`, 'error')
    }
  }

  async function handleReject(id) {
    try {
      await execute(`remediation/${id}/reject`)
      setActions(prev => prev.filter(a => a.action_id !== id))
      showToast(`Action #${id} rejected`)
    } catch (err) {
      showToast(`Failed: ${err.message}`, 'error')
    }
  }

  return (
    <div>
      {ToastComponent}
      <h2 className="text-2xl font-bold mb-2">Remediation</h2>
      <p className="text-sm text-gray-500 mb-6">
        Pending recommendations from the integrity monitor. Approve to apply immediately, or reject to dismiss.
      </p>

      {loading && !data && (
        <div className="text-gray-500 text-sm">Loading...</div>
      )}

      {actions.length === 0 && !loading && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-8 text-center">
          <p className="text-gray-400 text-lg mb-2">All clear</p>
          <p className="text-gray-600 text-sm">No pending recommendations. The integrity monitor will create them when issues are detected.</p>
        </div>
      )}

      <div className="grid gap-4">
        {actions.map(action => {
          const badge = TYPE_BADGES[action.action_type] || TYPE_BADGES.config_change
          return (
            <div key={action.action_id} className="bg-gray-900 border border-gray-800 rounded-lg p-5">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs text-gray-500">#{action.action_id}</span>
                    <span className={`text-xs px-2 py-0.5 rounded ${badge.color}`}>{badge.label}</span>
                  </div>
                  <h3 className="text-white font-medium">{action.title}</h3>
                </div>
              </div>
              <p className="text-sm text-gray-400 mb-4 leading-relaxed">{action.detail}</p>
              {action.config_key && (
                <p className="text-xs text-gray-600 mb-4 font-mono">
                  {action.config_key}{action.config_value != null ? ` → ${action.config_value}` : ''}
                </p>
              )}
              <div className="flex gap-2">
                <button
                  onClick={() => handleApprove(action.action_id)}
                  disabled={cmdLoading}
                  className="flex-1 px-4 py-2 text-sm font-medium bg-green-600 hover:bg-green-500 disabled:bg-gray-700 text-white rounded transition-colors"
                >
                  Approve
                </button>
                <button
                  onClick={() => handleReject(action.action_id)}
                  disabled={cmdLoading}
                  className="flex-1 px-4 py-2 text-sm text-gray-400 bg-gray-800 hover:bg-gray-700 disabled:bg-gray-800 rounded transition-colors"
                >
                  Reject
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
