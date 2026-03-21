import { useState, useCallback } from 'react'

/**
 * Hook for sending POST commands to the bot via the dashboard API.
 * Returns execute function, loading state, error, and last result.
 *
 * Usage:
 *   const { execute, loading, error, result } = useCommand()
 *   await execute('pause')
 *   await execute('config', { key: 'min_to_trade', value: 70 })
 */
export function useCommand() {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  const execute = useCallback(async (endpoint, body = null) => {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const opts = {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      }
      if (body) opts.body = JSON.stringify(body)

      const resp = await fetch(`/api/cmd/${endpoint}`, opts)
      const data = await resp.json()

      if (!resp.ok) {
        const msg = data?.detail || `HTTP ${resp.status}`
        setError(msg)
        throw new Error(msg)
      }

      setResult(data)
      return data
    } catch (err) {
      if (!error) setError(err.message)
      throw err
    } finally {
      setLoading(false)
    }
  }, [])

  return { execute, loading, error, result }
}
