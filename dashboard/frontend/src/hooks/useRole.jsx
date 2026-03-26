import { useState, useEffect, createContext, useContext } from 'react'

const RoleContext = createContext({ role: 'guest', email: '' })

/**
 * Provider that fetches the user's role from /api/me on mount.
 * Owner = full access, Guest = read-only.
 */
export function RoleProvider({ children }) {
  const [user, setUser] = useState({ role: 'guest', email: '' })

  useEffect(() => {
    fetch('/api/me')
      .then(r => r.json())
      .then(data => setUser({ role: data.role || 'guest', email: data.email || '' }))
      .catch(() => setUser({ role: 'guest', email: '' }))
  }, [])

  return <RoleContext.Provider value={user}>{children}</RoleContext.Provider>
}

/**
 * Hook to get the current user's role.
 * Returns { role: 'owner'|'guest', email: string, isOwner: boolean }
 */
export function useRole() {
  const user = useContext(RoleContext)
  return { ...user, isOwner: user.role === 'owner' }
}
