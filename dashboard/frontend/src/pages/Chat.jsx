import { useState, useRef, useEffect } from 'react'

// Storage key for persisting chat across page navigation
const STORAGE_KEY = 'ai_chat_state'

function loadChatState() {
  try {
    const saved = sessionStorage.getItem(STORAGE_KEY)
    if (saved) return JSON.parse(saved)
  } catch { /* ignore corrupt data */ }
  return { messages: [], sessionId: null }
}

function saveChatState(messages, sessionId) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ messages, sessionId }))
  } catch { /* storage full or unavailable */ }
}

export default function Chat() {
  const initial = useRef(loadChatState())
  const [messages, setMessages] = useState(initial.current.messages)
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState(initial.current.sessionId)
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)

  // Persist chat state whenever messages or sessionId change
  useEffect(() => {
    saveChatState(messages, sessionId)
  }, [messages, sessionId])

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  async function handleSend() {
    const text = input.trim()
    if (!text || loading) return

    setInput('')
    setMessages(prev => [...prev, { role: 'user', content: text }])
    setLoading(true)

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: sessionId }),
      })
      const data = await resp.json()

      if (!resp.ok) {
        setMessages(prev => [...prev, { role: 'error', content: data?.detail || 'Chat failed' }])
      } else {
        setSessionId(data.session_id)
        setMessages(prev => [...prev, { role: 'assistant', content: data.reply }])
      }
    } catch (err) {
      setMessages(prev => [...prev, { role: 'error', content: err.message }])
    } finally {
      setLoading(false)
      inputRef.current?.focus()
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)]">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-2xl font-bold">AI Chat</h2>
        {messages.length > 0 && (
          <button
            onClick={() => {
              setMessages([])
              setSessionId(null)
              sessionStorage.removeItem(STORAGE_KEY)
            }}
            className="px-3 py-1.5 text-xs text-gray-400 hover:text-white bg-gray-800 hover:bg-gray-700 rounded transition-colors"
          >
            Clear Chat
          </button>
        )}
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto mb-4 space-y-3 pr-2">
        {messages.length === 0 && (
          <div className="text-center py-12">
            <p className="text-gray-500 text-lg mb-2">Ask me anything about your trading</p>
            <div className="space-y-1 text-sm text-gray-600">
              <p>"How did EUR/USD do this week?"</p>
              <p>"What's my best performing pair?"</p>
              <p>"Why am I losing on SELL trades?"</p>
              <p>"Should I raise my confidence threshold?"</p>
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <ChatBubble key={i} role={msg.role} content={msg.content} />
        ))}

        {loading && (
          <div className="flex items-center gap-2 px-4 py-3">
            <div className="flex gap-1">
              <div className="w-2 h-2 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
              <div className="w-2 h-2 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
              <div className="w-2 h-2 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
            </div>
            <span className="text-sm text-gray-500">Thinking...</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className="flex gap-2">
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your trading..."
          rows={1}
          className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-4 py-3 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 resize-none"
        />
        <button
          onClick={handleSend}
          disabled={!input.trim() || loading}
          className="px-4 py-3 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded-lg transition-colors"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
          </svg>
        </button>
      </div>
    </div>
  )
}

function ChatBubble({ role, content }) {
  if (role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] bg-blue-600 rounded-lg rounded-br-sm px-4 py-2.5">
          <p className="text-sm text-white whitespace-pre-wrap">{content}</p>
        </div>
      </div>
    )
  }

  if (role === 'error') {
    return (
      <div className="flex justify-start">
        <div className="max-w-[80%] bg-red-900/30 border border-red-800 rounded-lg rounded-bl-sm px-4 py-2.5">
          <p className="text-sm text-red-400">{content}</p>
        </div>
      </div>
    )
  }

  // Assistant
  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] bg-gray-800 rounded-lg rounded-bl-sm px-4 py-2.5">
        <p className="text-sm text-gray-200 whitespace-pre-wrap leading-relaxed">{content}</p>
      </div>
    </div>
  )
}
