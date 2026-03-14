import { useApi } from '../hooks/useApi'

export default function Config() {
  const { data, loading, error } = useApi('/api/config')

  if (loading) return <p className="text-gray-400">Loading config...</p>
  if (error) return <p className="text-red-400">Error: {error}</p>
  if (data?.error) return <p className="text-yellow-400">{data.error}</p>

  const config = data?.config || {}

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">Configuration</h2>
        <span className="text-xs text-gray-500 bg-gray-800 px-2 py-1 rounded">Read-only</span>
      </div>

      {Object.entries(config).map(([section, values]) => (
        <div key={section} className="mb-6">
          <h3 className="text-sm text-gray-500 uppercase tracking-wide mb-3">{section}</h3>
          <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <tbody>
                {Object.entries(values).map(([key, value]) => (
                  <tr key={key} className="border-b border-gray-800/50 last:border-0">
                    <td className="px-4 py-2.5 text-gray-400 w-1/3">{key}</td>
                    <td className="px-4 py-2.5 font-mono text-white">
                      <ConfigValue value={value} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  )
}

function ConfigValue({ value }) {
  if (value === null || value === undefined) {
    return <span className="text-gray-600">null</span>
  }
  if (typeof value === 'boolean') {
    return <span className={value ? 'text-green-400' : 'text-red-400'}>
      {value.toString()}
    </span>
  }
  if (Array.isArray(value)) {
    return <span>{value.join(', ')}</span>
  }
  if (typeof value === 'object') {
    return (
      <pre className="text-xs bg-gray-800 p-2 rounded whitespace-pre-wrap">
        {JSON.stringify(value, null, 2)}
      </pre>
    )
  }
  return <span>{String(value)}</span>
}
