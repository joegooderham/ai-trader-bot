import { useState, useEffect, useRef } from 'react'
import { useApi } from '../hooks/useApi'

const PAIRS = ['EUR_USD', 'GBP_USD', 'USD_JPY', 'AUD_USD', 'GBP_JPY']
const STARTING_BALANCE = 10000

const WIN_MESSAGES = [
  "SHOW ME THE MONEY! 💰",
  "Cha-ching! You're a natural! 🤑",
  "Warren Buffett called — he wants tips! 📈",
  "To the moon! 🚀",
  "Big brain energy! 🧠",
  "The Wolf of Wall Street! 🐺",
  "Money printer go brrr! 💸",
  "You son of a gun, you did it! 🎯",
]

const LOSS_MESSAGES = [
  "Ouch... that's going to leave a mark 😬",
  "The market giveth, and the market taketh away 📉",
  "Buy the dip? That WAS the dip... 🕳️",
  "HODL... oh wait, wrong market 😅",
  "Even the bot loses sometimes! 🤖",
  "That's what stop-losses are for! 🛑",
  "Pain is temporary, profit is... hopefully next time 🤞",
]

const EVEN_MESSAGES = [
  "Breakeven — the most boring outcome 😐",
  "Not a loss! That's technically a win... right? 🤔",
  "The market said: meh 🫤",
]

function randomMsg(arr) {
  return arr[Math.floor(Math.random() * arr.length)]
}

export default function TradingGame() {
  const [selectedPair, setSelectedPair] = useState('EUR_USD')
  const [balance, setBalance] = useState(STARTING_BALANCE)
  const [position, setPosition] = useState(null) // {direction, entry, pair, size, openedAt}
  const [history, setHistory] = useState([])
  const [message, setMessage] = useState(null)
  const [streak, setStreak] = useState(0)
  const messageTimer = useRef(null)

  // Fetch live prices
  const { data: priceData } = useApi('/api/cmd/positions', 5000)
  const { data: overviewData } = useApi('/api/overview', 10000)

  // Simulate price from real market data
  const [prices, setPrices] = useState({})

  useEffect(() => {
    // Use real candle data to get approximate prices
    async function fetchPrices() {
      try {
        const resp = await fetch('/api/positions/live')
        const data = await resp.json()
        // Extract prices from any source available
        const newPrices = { ...prices }
        // Also fetch from overview for pairs not in positions
        const overResp = await fetch('/api/overview')
        const overData = await overResp.json()

        // Generate simulated prices with small random walk for game feel
        for (const pair of PAIRS) {
          const basePrices = {
            'EUR_USD': 1.0850, 'GBP_USD': 1.2920, 'USD_JPY': 150.50,
            'AUD_USD': 0.6530, 'GBP_JPY': 194.50,
          }
          if (!newPrices[pair]) {
            newPrices[pair] = basePrices[pair] || 1.0
          }
          // Random walk: ±0.02% per tick for game feel
          const change = (Math.random() - 0.5) * 0.0004 * newPrices[pair]
          newPrices[pair] = parseFloat((newPrices[pair] + change).toFixed(
            pair.includes('JPY') ? 3 : 5
          ))
        }
        setPrices(newPrices)
      } catch {
        // Generate prices anyway for the game
        setPrices(prev => {
          const updated = { ...prev }
          for (const pair of PAIRS) {
            if (!updated[pair]) {
              updated[pair] = pair.includes('JPY') ? 150.5 : 1.085
            }
            const change = (Math.random() - 0.5) * 0.0004 * updated[pair]
            updated[pair] = parseFloat((updated[pair] + change).toFixed(
              pair.includes('JPY') ? 3 : 5
            ))
          }
          return updated
        })
      }
    }

    fetchPrices()
    const interval = setInterval(fetchPrices, 2000)
    return () => clearInterval(interval)
  }, [])

  // Calculate live P&L on open position
  const currentPrice = prices[selectedPair] || 0
  let livePL = 0
  if (position && currentPrice) {
    const diff = position.direction === 'BUY'
      ? currentPrice - position.entry
      : position.entry - currentPrice
    const pipSize = position.pair.includes('JPY') ? 0.01 : 0.0001
    livePL = (diff / pipSize) * position.size * 0.10 // $0.10 per pip per micro lot
  }

  function openTrade(direction) {
    if (position) return // Already in a trade
    const price = prices[selectedPair]
    if (!price) return

    setPosition({
      direction,
      entry: price,
      pair: selectedPair,
      size: 10, // 10 micro lots
      openedAt: new Date(),
    })
    setMessage(null)
  }

  function closeTrade() {
    if (!position) return
    const price = prices[position.pair]
    if (!price) return

    const diff = position.direction === 'BUY'
      ? price - position.entry
      : position.entry - price
    const pipSize = position.pair.includes('JPY') ? 0.01 : 0.0001
    const pl = (diff / pipSize) * position.size * 0.10
    const roundedPL = parseFloat(pl.toFixed(2))

    setBalance(prev => prev + roundedPL)

    const trade = {
      pair: position.pair,
      direction: position.direction,
      entry: position.entry,
      exit: price,
      pl: roundedPL,
      closedAt: new Date(),
    }
    setHistory(prev => [trade, ...prev].slice(0, 20))

    // Fun messages
    if (roundedPL > 5) {
      setMessage({ text: randomMsg(WIN_MESSAGES), type: 'win' })
      setStreak(prev => prev + 1)
    } else if (roundedPL < -5) {
      setMessage({ text: randomMsg(LOSS_MESSAGES), type: 'loss' })
      setStreak(0)
    } else {
      setMessage({ text: randomMsg(EVEN_MESSAGES), type: 'even' })
    }

    // Clear message after 4 seconds
    if (messageTimer.current) clearTimeout(messageTimer.current)
    messageTimer.current = setTimeout(() => setMessage(null), 4000)

    setPosition(null)
  }

  const totalPL = balance - STARTING_BALANCE
  const wins = history.filter(t => t.pl > 0).length
  const losses = history.filter(t => t.pl < 0).length

  return (
    <div>
      <h2 className="text-2xl font-bold mb-2">Trading Simulator</h2>
      <p className="text-sm text-gray-500 mb-6">
        Think you can beat the bot? Trade with virtual money and see how you do.
      </p>

      {/* Fun message overlay */}
      {message && (
        <div className={`mb-4 p-4 rounded-lg text-center text-lg font-bold animate-bounce ${
          message.type === 'win' ? 'bg-green-900/30 text-green-400 border border-green-800' :
          message.type === 'loss' ? 'bg-red-900/30 text-red-400 border border-red-800' :
          'bg-gray-800 text-gray-400 border border-gray-700'
        }`}>
          {message.text}
          {streak >= 3 && message.type === 'win' && (
            <div className="text-sm mt-1">🔥 {streak} win streak!</div>
          )}
        </div>
      )}

      {/* Balance + Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 text-center">
          <p className="text-xs text-gray-500">Balance</p>
          <p className="text-xl font-bold font-mono text-white">${balance.toFixed(2)}</p>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 text-center">
          <p className="text-xs text-gray-500">Total P&L</p>
          <p className={`text-xl font-bold font-mono ${totalPL >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {totalPL >= 0 ? '+' : ''}{totalPL.toFixed(2)}
          </p>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 text-center">
          <p className="text-xs text-gray-500">Trades</p>
          <p className="text-xl font-bold text-white">{history.length}</p>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 text-center">
          <p className="text-xs text-gray-500">Win Rate</p>
          <p className="text-xl font-bold text-white">
            {history.length > 0 ? Math.round(wins / history.length * 100) : 0}%
          </p>
        </div>
      </div>

      {/* Trading Panel */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 mb-6">
        {/* Pair selector */}
        <div className="flex gap-2 mb-4">
          {PAIRS.map(pair => (
            <button
              key={pair}
              onClick={() => !position && setSelectedPair(pair)}
              className={`px-3 py-1.5 text-xs rounded transition-colors ${
                selectedPair === pair
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
              } ${position ? 'opacity-50' : ''}`}
            >
              {pair.replace('_', '/')}
            </button>
          ))}
        </div>

        {/* Price display */}
        <div className="text-center mb-6">
          <p className="text-gray-500 text-sm">{selectedPair.replace('_', '/')}</p>
          <p className="text-4xl font-bold font-mono text-white my-2">
            {currentPrice || '...'}
          </p>
          {position && (
            <p className={`text-lg font-bold font-mono ${livePL >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {livePL >= 0 ? '+' : ''}{livePL.toFixed(2)}
              <span className="text-sm text-gray-500 ml-2">unrealised</span>
            </p>
          )}
        </div>

        {/* Trade buttons */}
        {!position ? (
          <div className="flex gap-3">
            <button
              onClick={() => openTrade('BUY')}
              className="flex-1 py-4 bg-green-600 hover:bg-green-500 text-white text-lg font-bold rounded-lg transition-colors"
            >
              📈 BUY
            </button>
            <button
              onClick={() => openTrade('SELL')}
              className="flex-1 py-4 bg-red-600 hover:bg-red-500 text-white text-lg font-bold rounded-lg transition-colors"
            >
              📉 SELL
            </button>
          </div>
        ) : (
          <div>
            <div className="flex items-center justify-between mb-3 text-sm text-gray-400">
              <span>
                {position.direction === 'BUY' ? '📈' : '📉'} {position.direction} {position.pair.replace('_', '/')} @ {position.entry}
              </span>
              <span>{position.size} micro lots</span>
            </div>
            <button
              onClick={closeTrade}
              className={`w-full py-4 text-lg font-bold rounded-lg transition-colors ${
                livePL >= 0
                  ? 'bg-green-600 hover:bg-green-500 text-white'
                  : 'bg-red-600 hover:bg-red-500 text-white'
              }`}
            >
              {livePL >= 0 ? '💰 CLOSE — Take Profit' : '🛑 CLOSE — Cut Loss'}
              <span className="ml-2 font-mono">{livePL >= 0 ? '+' : ''}{livePL.toFixed(2)}</span>
            </button>
          </div>
        )}
      </div>

      {/* Trade History */}
      {history.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
          <h3 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">
            Your Trades ({wins}W / {losses}L)
          </h3>
          <div className="space-y-1 max-h-60 overflow-y-auto">
            {history.map((t, i) => (
              <div key={i} className="flex items-center justify-between text-sm px-2 py-1.5 rounded bg-gray-800/30">
                <div className="flex items-center gap-2">
                  <span className={`text-xs px-1.5 py-0.5 rounded ${
                    t.direction === 'BUY' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                  }`}>{t.direction}</span>
                  <span className="text-gray-300">{t.pair.replace('_', '/')}</span>
                  <span className="text-xs text-gray-600">{t.entry} → {t.exit}</span>
                </div>
                <span className={`font-mono font-bold ${
                  t.pl > 0 ? 'text-green-400' : t.pl < 0 ? 'text-red-400' : 'text-gray-500'
                }`}>
                  {t.pl >= 0 ? '+' : ''}{t.pl.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
