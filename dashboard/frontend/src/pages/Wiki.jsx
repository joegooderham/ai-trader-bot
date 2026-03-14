import { Link } from 'react-router-dom'
import { useApi } from '../hooks/useApi'

export default function Wiki() {
  const { data, loading, error } = useApi('/api/wiki')

  if (loading) return <p className="text-gray-400">Loading wiki...</p>
  if (error) return <p className="text-red-400">Error: {error}</p>

  const pages = data?.pages || []

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">Wiki</h2>

      {data?.error && (
        <p className="text-yellow-400 text-sm mb-4">{data.error}</p>
      )}

      {pages.length === 0 ? (
        <p className="text-gray-500">No wiki pages found. The wiki will be cloned on first startup.</p>
      ) : (
        <div className="grid gap-3">
          {pages.map(page => (
            <Link
              key={page.name}
              to={`/wiki/${page.name}`}
              className="block bg-gray-900 border border-gray-800 rounded-lg p-4 hover:border-gray-600 transition-colors"
            >
              <h3 className="text-white font-medium">{page.title}</h3>
              <p className="text-gray-500 text-sm mt-1">{page.name}.md</p>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
