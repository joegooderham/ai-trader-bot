import { useState, useEffect } from 'react'
import { NavLink, useLocation } from 'react-router-dom'

const navItems = [
  { to: '/', label: 'Overview' },
  { to: '/positions', label: 'Positions' },
  { to: '/trades', label: 'Trades' },
  { to: '/summary', label: 'Summary' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/wiki', label: 'Wiki' },
  { to: '/config', label: 'Config' },
]

export default function Layout({ children }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const location = useLocation()

  // Close sidebar on navigation (mobile)
  useEffect(() => {
    setSidebarOpen(false)
  }, [location.pathname])

  return (
    <div className="min-h-screen flex">
      {/* Mobile header bar */}
      <div className="lg:hidden fixed top-0 left-0 right-0 z-30 bg-gray-900 border-b border-gray-800 px-4 py-3 flex items-center justify-between">
        <h1 className="text-lg font-bold text-white">AI Trader</h1>
        <button
          onClick={() => setSidebarOpen(!sidebarOpen)}
          className="text-gray-400 hover:text-white p-1"
          aria-label="Toggle menu"
        >
          {sidebarOpen ? (
            /* X icon */
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          ) : (
            /* Hamburger icon */
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          )}
        </button>
      </div>

      {/* Overlay for mobile sidebar */}
      {sidebarOpen && (
        <div
          className="lg:hidden fixed inset-0 bg-black/50 z-30"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <nav className={`
        w-56 bg-gray-900 border-r border-gray-800 flex flex-col fixed h-full z-40
        transition-transform duration-200
        ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
        lg:translate-x-0
      `}>
        <div className="p-4 border-b border-gray-800">
          <h1 className="text-lg font-bold text-white">AI Trader</h1>
          <p className="text-xs text-gray-500 mt-1">Dashboard v1.0</p>
        </div>
        <div className="flex-1 py-4 overflow-y-auto">
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

      {/* Main content — offset for sidebar on desktop, offset for header on mobile */}
      <main className="lg:ml-56 flex-1 p-4 md:p-6 mt-14 lg:mt-0">
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
