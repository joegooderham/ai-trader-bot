import { useState, useEffect } from 'react'

/**
 * Simple data fetching hook with auto-refresh.
 * @param {string} url - API endpoint (e.g., '/api/overview')
 * @param {number} refreshInterval - Auto-refresh interval in ms (0 = no refresh)
 */
export function useApi(url, refreshInterval = 0) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false

    async function fetchData() {
      try {
        const resp = await fetch(url)
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const json = await resp.json()
        if (!cancelled) {
          setData(json)
          setError(null)
          setLoading(false)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message)
          setLoading(false)
        }
      }
    }

    fetchData()

    // Auto-refresh if interval is set
    let timer
    if (refreshInterval > 0) {
      timer = setInterval(fetchData, refreshInterval)
    }

    return () => {
      cancelled = true
      if (timer) clearInterval(timer)
    }
  }, [url, refreshInterval])

  return { data, loading, error }
}
