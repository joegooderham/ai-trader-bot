import { useParams, Link } from 'react-router-dom'
import { useEffect, useRef } from 'react'
import { useApi } from '../hooks/useApi'

export default function WikiPage() {
  const { pageName } = useParams()
  const { data, loading, error } = useApi(`/api/wiki/${pageName}`)
  const contentRef = useRef(null)

  // After HTML is injected, find Mermaid code blocks and render them as diagrams
  useEffect(() => {
    if (!data?.html || !contentRef.current) return

    // Find all <code class="language-mermaid"> blocks (from fenced ```mermaid blocks)
    // The markdown library wraps them in <pre><code class="language-mermaid">
    const codeBlocks = contentRef.current.querySelectorAll('code.language-mermaid')
    if (codeBlocks.length === 0) return

    // Load Mermaid from CDN if not already loaded
    if (!window.mermaid) {
      const script = document.createElement('script')
      script.src = 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js'
      script.onload = () => {
        window.mermaid.initialize({
          startOnLoad: false,
          theme: 'dark',
          themeVariables: {
            primaryColor: '#1e40af',
            primaryTextColor: '#fff',
            primaryBorderColor: '#3b82f6',
            lineColor: '#6b7280',
            secondaryColor: '#7c3aed',
            tertiaryColor: '#059669',
          },
        })
        renderMermaidBlocks(codeBlocks)
      }
      document.head.appendChild(script)
    } else {
      renderMermaidBlocks(codeBlocks)
    }
  }, [data?.html])

  function renderMermaidBlocks(codeBlocks) {
    codeBlocks.forEach((block, i) => {
      const pre = block.parentElement
      if (!pre || pre.dataset.mermaidRendered) return

      // textContent automatically decodes HTML entities (&gt; → >, &amp; → &)
      // which is needed because the markdown renderer encodes arrows like -->
      const graphDefinition = block.textContent
      const container = document.createElement('div')
      container.className = 'mermaid-diagram my-4'

      try {
        window.mermaid.render(`mermaid-${pageName}-${i}`, graphDefinition).then(({ svg }) => {
          container.innerHTML = svg
          pre.replaceWith(container)
        }).catch(() => {
          // If render fails, leave the code block as-is
        })
      } catch {
        // Sync error — leave code block
      }

      pre.dataset.mermaidRendered = 'true'
    })
  }

  if (loading) return <p className="text-gray-400">Loading page...</p>
  if (error) return <p className="text-red-400">Error: {error}</p>

  return (
    <div>
      <Link
        to="/wiki"
        className="text-sm text-blue-400 hover:text-blue-300 mb-4 inline-block"
      >
        &larr; Back to Docs
      </Link>

      <div
        ref={contentRef}
        className="wiki-content bg-gray-900 border border-gray-800 rounded-lg p-6"
        dangerouslySetInnerHTML={{ __html: data?.html || '' }}
      />
    </div>
  )
}
