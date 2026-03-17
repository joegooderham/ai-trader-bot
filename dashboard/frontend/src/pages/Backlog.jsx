import { useApi } from '../hooks/useApi'

export default function Backlog() {
  const { data, loading, error } = useApi('/api/wiki/Backlog-and-Roadmap')

  if (loading) return <p className="text-gray-400">Loading backlog...</p>
  if (error) return <FallbackBacklog />

  return (
    <div>
      <h2 className="text-xl md:text-2xl font-bold mb-4 md:mb-6">Backlog & Roadmap</h2>
      <div
        className="prose prose-invert prose-sm max-w-none
          prose-headings:text-white prose-h2:text-lg prose-h3:text-base
          prose-a:text-blue-400 prose-strong:text-white
          prose-li:text-gray-300 prose-p:text-gray-300
          prose-table:border-gray-800 prose-th:text-gray-400 prose-td:text-gray-300"
        dangerouslySetInnerHTML={{ __html: data?.html || '' }}
      />
    </div>
  )
}

function FallbackBacklog() {
  return (
    <div>
      <h2 className="text-xl md:text-2xl font-bold mb-4 md:mb-6">Backlog & Roadmap</h2>
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <p className="text-gray-400 text-sm">
          Backlog page not available — check the{' '}
          <a
            href="https://github.com/joegooderham/ai-trader-bot/wiki/Backlog-and-Roadmap"
            className="text-blue-400 hover:underline"
            target="_blank"
            rel="noopener noreferrer"
          >
            GitHub Wiki
          </a>
        </p>
      </div>
    </div>
  )
}
