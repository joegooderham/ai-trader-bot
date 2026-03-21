import { useState, useEffect } from 'react'
import { NavLink, Link, useLocation } from 'react-router-dom'
import RunningPL from './RunningPL'
import { useApi } from '../hooks/useApi'

const navGroups = [
  {
    label: 'Trading',
    items: [
      { to: '/', label: 'Overview' },
      { to: '/positions', label: 'Positions' },
      { to: '/trades', label: 'Trades' },
      { to: '/journal', label: 'Journal' },
    ],
  },
  {
    label: 'Analytics',
    items: [
      { to: '/analytics', label: 'LSTM Analytics' },
      { to: '/heatmap', label: 'Heatmap' },
      { to: '/sessions', label: 'Sessions' },
    ],
  },
  {
    label: 'Tools',
    items: [
      { to: '/chat', label: 'AI Chat' },
      { to: '/what-if', label: 'What-If Simulator' },
      { to: '/remediation', label: 'Remediation', badge: true },
      { to: '/config', label: 'Config' },
    ],
  },
  {
    label: 'Docs',
    items: [
      { to: '/summary', label: 'Summary' },
      { to: '/wiki', label: 'Wiki' },
      { to: '/backlog', label: 'Backlog' },
    ],
  },
]

export default function Layout({ children }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const location = useLocation()

  // Fetch remediation count for badge
  const { data: remData } = useApi('/api/cmd/remediation', 30000)
  const remCount = remData?.pending_actions?.length || 0

  // Close sidebar on navigation (mobile)
  useEffect(() => {
    setSidebarOpen(false)
  }, [location.pathname])

  return (
    <div className="min-h-screen flex">
      {/* Mobile header bar */}
      <div className="lg:hidden fixed top-0 left-0 right-0 z-30 bg-gray-900 border-b border-gray-800 px-4 py-3 flex items-center justify-between">
        <Link to="/" className="text-lg font-bold text-white">AI Trader</Link>
        <div className="flex items-center gap-3">
          <RunningPL />
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
        <Link to="/" className="block p-4 border-b border-gray-800 hover:bg-gray-800 transition-colors">
          <h1 className="text-lg font-bold text-white">AI Trader</h1>
          <p className="text-xs text-gray-500 mt-1">Dashboard v2.0</p>
        </Link>
        <div className="flex-1 py-2 overflow-y-auto">
          {navGroups.map(group => (
            <div key={group.label} className="mb-1">
              <p className="px-4 py-1.5 text-[10px] font-semibold text-gray-600 uppercase tracking-wider">
                {group.label}
              </p>
              {group.items.map(({ to, label, badge }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={to === '/'}
                  className={({ isActive }) =>
                    `flex items-center justify-between px-4 py-2 text-sm transition-colors ${
                      isActive
                        ? 'bg-blue-600/20 text-blue-400 border-r-2 border-blue-400'
                        : 'text-gray-400 hover:text-white hover:bg-gray-800'
                    }`
                  }
                >
                  <span>{label}</span>
                  {badge && remCount > 0 && (
                    <span className="bg-red-500 text-white text-[10px] font-bold w-5 h-5 rounded-full flex items-center justify-center">
                      {remCount}
                    </span>
                  )}
                </NavLink>
              ))}
            </div>
          ))}
        </div>
        <div className="p-4 border-t border-gray-800 space-y-3">
          <StatusIndicator />
          <button
            onClick={() => window.location.href = '/cdn-cgi/access/logout'}
            className="w-full flex items-center gap-2 px-2 py-1.5 text-xs text-gray-500 hover:text-red-400 hover:bg-gray-800 rounded transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
            </svg>
            Log Out
          </button>
        </div>
      </nav>

      {/* Main content — offset for sidebar on desktop, offset for header on mobile */}
      <main className="lg:ml-56 flex-1 p-4 md:p-6 mt-14 lg:mt-0">
        {/* Running P&L in top-right corner on desktop */}
        <div className="hidden lg:flex justify-end mb-4">
          <RunningPL />
        </div>
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
