/**
 * Reusable stat card for dashboard metrics.
 * Shows a label, value, and optional sub-text.
 */
export default function StatCard({ label, value, sub, className = '' }) {
  return (
    <div className={`bg-gray-900 border border-gray-800 rounded-lg p-4 ${className}`}>
      <p className="text-xs text-gray-500 uppercase tracking-wide">{label}</p>
      <p className="text-2xl font-bold mt-1">{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  )
}
