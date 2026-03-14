import { useApi } from '../hooks/useApi'
import StatCard from '../components/StatCard'

export default function Analytics() {
  const { data: model } = useApi('/api/analytics/model', 60000)
  const { data: accuracy } = useApi('/api/analytics/accuracy', 60000)
  const { data: drift } = useApi('/api/analytics/drift', 60000)
  const { data: performance } = useApi('/api/analytics/performance', 60000)

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">LSTM Analytics</h2>

      {/* Model Info */}
      <Section title="Model Info">
        {model?.error ? (
          <p className="text-gray-500">MCP server unavailable</p>
        ) : model ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              label="Training Accuracy"
              value={model.accuracy ? `${(model.accuracy * 100).toFixed(1)}%` : '—'}
            />
            <StatCard
              label="Parameters"
              value={model.parameters?.toLocaleString() || '—'}
            />
            <StatCard
              label="Last Trained"
              value={model.last_trained?.slice(0, 16) || '—'}
            />
            <StatCard
              label="Epochs"
              value={model.epochs || '—'}
            />
          </div>
        ) : (
          <p className="text-gray-400">Loading...</p>
        )}
      </Section>

      {/* Prediction Accuracy */}
      <Section title="Prediction Accuracy">
        {accuracy?.error ? (
          <p className="text-gray-500">No accuracy data yet</p>
        ) : accuracy ? (
          <div className="grid grid-cols-3 gap-4">
            <AccuracyCard window="24h" data={accuracy} />
            <AccuracyCard window="7d" data={accuracy} />
            <AccuracyCard window="30d" data={accuracy} />
          </div>
        ) : (
          <p className="text-gray-400">Loading...</p>
        )}
      </Section>

      {/* Drift Detection */}
      <Section title="Drift Detection">
        {drift?.error ? (
          <p className="text-gray-500">Drift detection unavailable</p>
        ) : drift ? (
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <div className="flex items-center gap-3 mb-3">
              <div className={`w-3 h-3 rounded-full ${
                drift.drift_detected ? 'bg-red-500' : 'bg-green-500'
              }`} />
              <span className="font-medium text-white">
                {drift.drift_detected ? 'Drift Detected' : 'No Drift'}
              </span>
            </div>
            {drift.live_accuracy !== undefined && (
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <p className="text-gray-500">Live Accuracy</p>
                  <p className="text-white font-mono">
                    {(drift.live_accuracy * 100).toFixed(1)}%
                  </p>
                </div>
                <div>
                  <p className="text-gray-500">Training Accuracy</p>
                  <p className="text-white font-mono">
                    {(drift.training_accuracy * 100).toFixed(1)}%
                  </p>
                </div>
              </div>
            )}
            {drift.should_retrain && (
              <p className="text-yellow-400 text-sm mt-3">
                Retrain recommended — live accuracy has degraded significantly
              </p>
            )}
          </div>
        ) : (
          <p className="text-gray-400">Loading...</p>
        )}
      </Section>

      {/* Performance Metrics */}
      <Section title="Performance Metrics">
        {performance?.error ? (
          <p className="text-gray-500">Performance data unavailable</p>
        ) : performance ? (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <StatCard
              label="LSTM Edge (avg)"
              value={performance.lstm_edge_avg !== undefined
                ? `${performance.lstm_edge_avg > 0 ? '+' : ''}${performance.lstm_edge_avg.toFixed(1)}pts`
                : '—'
              }
              sub="Confidence delta vs indicators-only"
            />
            <StatCard
              label="Agreement Rate"
              value={performance.lstm_indicator_agreement !== undefined
                ? `${(performance.lstm_indicator_agreement * 100).toFixed(0)}%`
                : '—'
              }
              sub="LSTM agrees with indicators"
            />
            <StatCard
              label="Weekly Trend"
              value={performance.accuracy_trend_weekly || '—'}
              sub="Accuracy direction"
            />
          </div>
        ) : (
          <p className="text-gray-400">Loading...</p>
        )}

        {/* Per-pair accuracy */}
        {performance?.pair_accuracy_7d && Object.keys(performance.pair_accuracy_7d).length > 0 && (
          <div className="mt-4 bg-gray-900 border border-gray-800 rounded-lg p-4">
            <h4 className="text-sm text-gray-500 uppercase tracking-wide mb-3">Per-Pair Accuracy (7d)</h4>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {Object.entries(performance.pair_accuracy_7d).map(([pair, acc]) => (
                <div key={pair} className="text-sm">
                  <p className="text-gray-400">{pair.replace('_', '/')}</p>
                  <p className="text-white font-mono font-medium">
                    {(acc * 100).toFixed(1)}%
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}
      </Section>
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div className="mb-8">
      <h3 className="text-sm text-gray-500 uppercase tracking-wide mb-3">{title}</h3>
      {children}
    </div>
  )
}

function AccuracyCard({ window, data }) {
  const key = `accuracy_${window}`
  const value = data[key]

  return (
    <StatCard
      label={`${window} Accuracy`}
      value={value !== undefined ? `${(value * 100).toFixed(1)}%` : '—'}
      className={value !== undefined && value < 0.5 ? 'border-red-800/50' : ''}
    />
  )
}
