import { useState, useEffect } from 'react'

/**
 * Toast notification component. Auto-dismisses after `duration` ms.
 * Usage: <Toast message="Success!" type="success" onClose={() => setToast(null)} />
 */
export default function Toast({ message, type = 'success', duration = 4000, onClose }) {
  useEffect(() => {
    const timer = setTimeout(onClose, duration)
    return () => clearTimeout(timer)
  }, [duration, onClose])

  const colors = {
    success: 'bg-green-600 border-green-500',
    error: 'bg-red-600 border-red-500',
    info: 'bg-blue-600 border-blue-500',
  }

  return (
    <div className="fixed top-4 right-4 z-50 animate-slide-in">
      <div className={`${colors[type] || colors.info} border rounded-lg px-4 py-3 shadow-lg max-w-sm`}>
        <div className="flex items-center gap-2">
          <span className="text-sm text-white">{message}</span>
          <button onClick={onClose} className="text-white/70 hover:text-white ml-2">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  )
}

/**
 * Hook for managing toast state.
 * Usage: const { toast, showToast, ToastComponent } = useToast()
 */
export function useToast() {
  const [toast, setToast] = useState(null)

  const showToast = (message, type = 'success') => {
    setToast({ message, type })
  }

  const ToastComponent = toast ? (
    <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />
  ) : null

  return { toast, showToast, ToastComponent }
}
