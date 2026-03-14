import { useState, useEffect } from 'react'
import { NavLink } from 'react-router-dom'

const navItems = [
  { to: '/', label: 'Overview' },
  { to: '/positions', label: 'Positions' },
  { to: '/trades', label: 'Trades' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/wiki', label: 'Wiki' },
  { to: '/config', label: 'Config' },
]

export default function Layout({ children }) {
  return (
    <div className="min-h-screen flex">
      {/* Sidebar */}
      <nav className="w-56 bg-gray-900 border-r border-gray-800 flex flex-col fixed h-full">
        <div className="p-4 border-b border-gray-800">
          <h1 className="text-lg font-bold text-white">AI Trader</h1>
          <p className="text-xs text-gray-500 mt-1">Dashboard v1.0</p>
        </div>
        <div className="flex-1 py-4">
          {navItems.map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `block px-4 py-2.5 text-sm transition-colors ${
                  isActive
                    ? 'bg-blue-600/20 text-blue-400 border-r-2 border-blue-400'
                    : 'text-gray-400 hover:text-white hover:bg-gray-800'
                }`
              }
            >
              {label}
            </NavLink>
          ))}
        </div>
        <div className="p-4 border-t border-gray-800">
          <StatusIndicator />
        </div>
      </nav>

      {/* Main content */}
      <main className="ml-56 flex-1 p-6">
        {children}
      </main>
    </div>
  )
}

function StatusIndicator() {
  const [status, setStatus] = useState('checking')

  useEffect(() => {
    fetch('/api/health')
      .then(r => r.json())
      .then(d => {
        const allOk = Object.values(d).every(v => v === 'ok')
        setStatus(allOk ? 'ok' : 'degraded')
      })
      .catch(() => setStatus('offline'))
  }, [])

  const colors = {
    ok: 'bg-green-500',
    degraded: 'bg-yellow-500',
    offline: 'bg-red-500',
    checking: 'bg-gray-500',
  }

  const labels = {
    ok: 'All Systems OK',
    degraded: 'Degraded',
    offline: 'Offline',
    checking: 'Checking...',
  }

  return (
    <div className="flex items-center gap-2 text-xs text-gray-400">
      <div className={`w-2 h-2 rounded-full ${colors[status]} animate-pulse`} />
      {labels[status]}
    </div>
  )
}
