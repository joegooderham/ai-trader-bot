import { useParams, Link } from 'react-router-dom'
import { useApi } from '../hooks/useApi'

export default function WikiPage() {
  const { pageName } = useParams()
  const { data, loading, error } = useApi(`/api/wiki/${pageName}`)

  if (loading) return <p className="text-gray-400">Loading page...</p>
  if (error) return <p className="text-red-400">Error: {error}</p>

  return (
    <div>
      <Link
        to="/wiki"
        className="text-sm text-blue-400 hover:text-blue-300 mb-4 inline-block"
      >
        &larr; Back to Wiki
      </Link>

      <div
        className="wiki-content bg-gray-900 border border-gray-800 rounded-lg p-6"
        dangerouslySetInnerHTML={{ __html: data?.html || '' }}
      />
    </div>
  )
}
