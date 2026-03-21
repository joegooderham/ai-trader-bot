import { useState, useEffect } from 'react'
import { useApi } from '../hooks/useApi'
import { useCommand } from '../hooks/useCommand'
import { useToast } from '../components/Toast'

const EDITABLE_FIELDS = [
  { key: 'min_to_trade', label: 'Min Confidence to Trade', unit: '%', min: 40, max: 90, step: 1, statusKey: 'min_confidence' },
  { key: 'per_trade_risk_pct', label: 'Risk Per Trade', unit: '%', min: 0.5, max: 5, step: 0.5, statusKey: 'per_trade_risk_pct' },
  { key: 'hold_overnight_threshold', label: 'Overnight Hold Threshold', unit: '%', min: 50, max: 100, step: 1, statusKey: 'hold_overnight_threshold' },
  { key: 'stop_loss_atr_multiplier', label: 'Stop-Loss ATR Multiplier', unit: 'x', min: 1, max: 4, step: 0.1, statusKey: 'stop_loss_atr_multiplier' },
  { key: 'trailing_stop_activation_atr', label: 'Trailing Stop Activation', unit: 'x ATR', min: 0.5, max: 3, step: 0.1, statusKey: 'trailing_stop_activation_atr' },
  { key: 'trailing_stop_trail_atr', label: 'Trailing Stop Distance', unit: 'x ATR', min: 0.5, max: 2.5, step: 0.1, statusKey: 'trailing_stop_trail_atr' },
  { key: 'take_profit_ratio', label: 'Take-Profit Ratio', unit: ':1', min: 1, max: 5, step: 0.5, statusKey: 'take_profit_ratio' },
  { key: 'lstm_shadow_mode', label: 'LSTM Shadow Mode', type: 'toggle', statusKey: 'lstm_shadow_mode' },
]

export default function ConfigEditor() {
  const { data: status } = useApi('/api/cmd/status', 15000)
  const { data: config } = useApi('/api/config')
  const { execute, loading } = useCommand()
  const { showToast, ToastComponent } = useToast()
  const [values, setValues] = useState({})

  // Sync values from bot status
  useEffect(() => {
    if (status) {
      const v = {}
      EDITABLE_FIELDS.forEach(f => {
        v[f.key] = status[f.statusKey]
      })
      setValues(v)
    }
  }, [status])

  async function handleSave(field) {
    try {
      await execute('config', { key: field.key, value: values[field.key] })
      showToast(`${field.label} updated to ${values[field.key]}${field.unit || ''}`)
    } catch (err) {
      showToast(`Failed: ${err.message}`, 'error')
    }
  }

  async function handleToggle(field) {
    const newVal = !values[field.key]
    setValues(prev => ({ ...prev, [field.key]: newVal }))
    try {
      await execute('config', { key: field.key, value: newVal })
      showToast(`${field.label}: ${newVal ? 'ON' : 'OFF'}`)
    } catch (err) {
      showToast(`Failed: ${err.message}`, 'error')
      setValues(prev => ({ ...prev, [field.key]: !newVal }))
    }
  }

  return (
    <div>
      {ToastComponent}
      <h2 className="text-2xl font-bold mb-6">Configuration</h2>

      {/* Editable runtime config */}
      <div className="mb-8">
        <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-4">
          Live Config — changes apply immediately
        </h3>
        <div className="grid gap-3">
          {EDITABLE_FIELDS.map(field => {
            if (field.type === 'toggle') {
              return (
                <div key={field.key} className="flex items-center justify-between bg-gray-900 border border-gray-800 rounded-lg p-4">
                  <span className="text-sm text-gray-300">{field.label}</span>
                  <button
                    onClick={() => handleToggle(field)}
                    disabled={loading}
                    className={`relative w-11 h-6 rounded-full transition-colors ${
                      values[field.key] ? 'bg-blue-600' : 'bg-gray-700'
                    }`}
                  >
                    <div className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                      values[field.key] ? 'translate-x-5' : ''
                    }`} />
                  </button>
                </div>
              )
            }

            return (
              <div key={field.key} className="bg-gray-900 border border-gray-800 rounded-lg p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm text-gray-300">{field.label}</span>
                  <span className="text-sm font-mono text-white">
                    {values[field.key] ?? '...'}{field.unit}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min={field.min}
                    max={field.max}
                    step={field.step}
                    value={values[field.key] ?? field.min}
                    onChange={e => setValues(prev => ({ ...prev, [field.key]: parseFloat(e.target.value) }))}
                    className="flex-1 accent-blue-500"
                  />
                  <button
                    onClick={() => handleSave(field)}
                    disabled={loading || values[field.key] === status?.[field.statusKey]}
                    className="px-3 py-1 text-xs bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded transition-colors"
                  >
                    Save
                  </button>
                </div>
                <div className="flex justify-between text-xs text-gray-600 mt-1">
                  <span>{field.min}{field.unit}</span>
                  <span>{field.max}{field.unit}</span>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Read-only full config */}
      {config && (
        <div>
          <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-4">
            Full Config (read-only)
          </h3>
          {Object.entries(config).map(([section, values]) => (
            <div key={section} className="mb-6">
              <h4 className="text-xs font-medium text-gray-500 uppercase mb-2">{section}</h4>
              <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
                <table className="w-full text-sm">
                  <tbody>
                    {typeof values === 'object' && values !== null ? (
                      Object.entries(values).map(([key, val]) => (
                        <tr key={key} className="border-t border-gray-800 first:border-0">
                          <td className="px-4 py-2 text-gray-400 w-1/3">{key}</td>
                          <td className="px-4 py-2 font-mono text-white">
                            {typeof val === 'boolean' ? (
                              <span className={val ? 'text-green-400' : 'text-red-400'}>
                                {String(val)}
                              </span>
                            ) : Array.isArray(val) ? val.join(', ') : String(val ?? 'null')}
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr><td className="px-4 py-2 font-mono text-white">{String(values)}</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
