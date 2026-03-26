import { useState, useEffect } from 'react'
import { useApi } from '../hooks/useApi'
import PLBadge from '../components/PLBadge'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'
]

export default function Calendar() {
  const today = new Date()
  const [year, setYear] = useState(today.getFullYear())
  const [month, setMonth] = useState(today.getMonth())
  const [selectedDate, setSelectedDate] = useState(null)
  const [dayDetail, setDayDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const { data } = useApi('/api/calendar?months=6')

  const calendarData = data || {}

  function prevMonth() {
    if (month === 0) { setMonth(11); setYear(y => y - 1) }
    else setMonth(m => m - 1)
    setSelectedDate(null)
    setDayDetail(null)
  }

  function nextMonth() {
    if (month === 11) { setMonth(0); setYear(y => y + 1) }
    else setMonth(m => m + 1)
    setSelectedDate(null)
    setDayDetail(null)
  }

  async function selectDay(dateStr, hasData) {
    if (!hasData) return
    if (selectedDate === dateStr) {
      setSelectedDate(null)
      setDayDetail(null)
      return
    }
    setSelectedDate(dateStr)
    setDetailLoading(true)
    try {
      const resp = await fetch(`/api/calendar/${dateStr}`)
      if (resp.ok) setDayDetail(await resp.json())
    } catch { /* ignore */ }
    finally { setDetailLoading(false) }
  }

  // Build calendar grid
  const firstDay = new Date(year, month, 1)
  const lastDay = new Date(year, month + 1, 0)
  const startDow = (firstDay.getDay() + 6) % 7
  const daysInMonth = lastDay.getDate()

  // Monthly totals
  let monthTrades = 0, monthPL = 0, monthWins = 0, monthLosses = 0
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`
    const day = calendarData[dateStr]
    if (day) {
      monthTrades += day.trades
      monthPL += day.net_pl
      monthWins += day.wins
      monthLosses += day.losses
    }
  }

  return (
    <div>
      <h2 className="text-2xl font-bold mb-2">P&L Calendar</h2>
      <p className="text-sm text-gray-500 mb-6">Tap any day to see trade details.</p>

      {/* Month navigation */}
      <div className="flex items-center justify-between mb-4">
        <button onClick={prevMonth} className="px-3 py-1.5 text-gray-400 hover:text-white bg-gray-800 hover:bg-gray-700 rounded transition-colors">
          &larr;
        </button>
        <div className="text-center">
          <h3 className="text-lg font-bold text-white">{MONTHS[month]} {year}</h3>
          <p className="text-sm text-gray-500">
            {monthTrades} trades | {monthWins}W {monthLosses}L | <PLBadge value={monthPL} />
          </p>
        </div>
        <button onClick={nextMonth} className="px-3 py-1.5 text-gray-400 hover:text-white bg-gray-800 hover:bg-gray-700 rounded transition-colors">
          &rarr;
        </button>
      </div>

      {/* Calendar grid */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
        <div className="grid grid-cols-7 border-b border-gray-800">
          {DAYS.map(d => (
            <div key={d} className="text-center text-xs text-gray-500 py-2 font-medium">{d}</div>
          ))}
        </div>

        <div className="grid grid-cols-7">
          {Array.from({ length: startDow }, (_, i) => (
            <div key={`empty-${i}`} className="border-b border-r border-gray-800/50 min-h-[80px] bg-gray-950/30" />
          ))}

          {Array.from({ length: daysInMonth }, (_, i) => {
            const d = i + 1
            const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`
            const day = calendarData[dateStr]
            const isToday = dateStr === today.toISOString().substring(0, 10)
            const isWeekend = new Date(year, month, d).getDay() % 6 === 0
            const isSelected = selectedDate === dateStr

            return (
              <div
                key={d}
                onClick={() => day && selectDay(dateStr, true)}
                className={`border-b border-r border-gray-800/50 min-h-[80px] p-1.5 transition-colors ${
                  isSelected ? 'bg-blue-900/40 border-blue-600/50 ring-1 ring-blue-500/30' :
                  isToday ? 'bg-blue-900/20 border-blue-800/50' :
                  isWeekend ? 'bg-gray-950/50' : ''
                } ${day ? 'cursor-pointer hover:bg-gray-800/50' : ''}`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className={`text-xs font-medium ${
                    isSelected ? 'text-blue-300' : isToday ? 'text-blue-400' : 'text-gray-500'
                  }`}>
                    {d}
                  </span>
                </div>

                {day ? (
                  <div>
                    <div className={`text-sm font-bold font-mono ${
                      day.net_pl > 0 ? 'text-green-400' : day.net_pl < 0 ? 'text-red-400' : 'text-gray-500'
                    }`}>
                      {day.net_pl >= 0 ? '+' : '-'}£{Math.abs(day.net_pl).toFixed(2)}
                    </div>
                    <div className="text-[10px] text-gray-500 mt-0.5">
                      {day.trades}T {day.wins}W {day.losses}L
                      {day.breakeven > 0 && ` ${day.breakeven}BE`}
                    </div>
                  </div>
                ) : isWeekend ? (
                  <span className="text-[10px] text-gray-700">Market closed</span>
                ) : null}
              </div>
            )
          })}
        </div>
      </div>

      {/* Day detail panel */}
      {selectedDate && (
        <DayDetail
          date={selectedDate}
          data={dayDetail}
          loading={detailLoading}
          onClose={() => { setSelectedDate(null); setDayDetail(null) }}
        />
      )}

      {/* Legend */}
      <div className="flex items-center gap-4 mt-3 text-xs text-gray-500">
        <div className="flex items-center gap-1"><span className="text-green-400 font-bold">+£</span> Profitable</div>
        <div className="flex items-center gap-1"><span className="text-red-400 font-bold">-£</span> Loss</div>
        <div>Tap a day for trade details</div>
      </div>
    </div>
  )
}

function DayDetail({ date, data, loading, onClose }) {
  if (loading) {
    return (
      <div className="mt-4 bg-gray-900 border border-gray-800 rounded-lg p-5 animate-pulse">
        <div className="h-4 bg-gray-800 rounded w-48 mb-3" />
        <div className="h-3 bg-gray-800 rounded w-32" />
      </div>
    )
  }

  if (!data) return null

  const { summary, trades } = data
  const dayName = new Date(date + 'T12:00:00').toLocaleDateString('en-GB', { weekday: 'long' })

  return (
    <div className="mt-4 bg-gray-900 border border-gray-800 rounded-lg p-5">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-white font-bold">{dayName} {date}</h3>
          <p className="text-sm text-gray-500">
            {summary.total} trades | {summary.wins}W {summary.losses}L {summary.breakeven > 0 ? `${summary.breakeven}BE` : ''} | {summary.win_rate}% win rate
          </p>
        </div>
        <div className="flex items-center gap-3">
          <PLBadge value={summary.net_pl} />
          <button onClick={onClose} className="text-gray-500 hover:text-white transition-colors">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      {/* Best/worst */}
      {(summary.best_trade || summary.worst_trade) && (
        <div className="flex gap-4 mb-4 text-xs">
          {summary.best_trade && (
            <span className="bg-green-900/20 text-green-400 px-2 py-1 rounded">Best: {summary.best_trade}</span>
          )}
          {summary.worst_trade && (
            <span className="bg-red-900/20 text-red-400 px-2 py-1 rounded">Worst: {summary.worst_trade}</span>
          )}
        </div>
      )}

      {/* Trade list */}
      <div className="space-y-2">
        {trades.map(t => (
          <TradeRow key={t.id} trade={t} />
        ))}
      </div>
    </div>
  )
}

function TradeRow({ trade }) {
  const [expanded, setExpanded] = useState(false)
  const pl = trade.pl || 0
  const isWin = pl > 0.01
  const isLoss = pl < -0.01
  const pair = (trade.pair || '').replace('_', '/')
  const time = (trade.opened_at || '').substring(11, 16)
  const duration = trade.duration_min
    ? trade.duration_min < 60 ? `${trade.duration_min}m` : `${Math.floor(trade.duration_min / 60)}h${trade.duration_min % 60}m`
    : ''

  return (
    <div
      className={`rounded border transition-colors cursor-pointer ${
        isWin ? 'border-green-900/40 hover:border-green-800' :
        isLoss ? 'border-red-900/40 hover:border-red-800' :
        'border-gray-800 hover:border-gray-700'
      }`}
      onClick={() => setExpanded(!expanded)}
    >
      {/* Summary row */}
      <div className="flex items-center justify-between px-3 py-2">
        <div className="flex items-center gap-2">
          <span className={`text-xs px-1.5 py-0.5 rounded ${
            trade.direction === 'BUY' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
          }`}>{trade.direction}</span>
          <span className="text-sm text-gray-300 font-medium">{pair}</span>
          <span className="text-xs text-gray-600">{time}</span>
          {duration && <span className="text-xs text-gray-600">{duration}</span>}
          {trade.rr_achieved > 0 && <span className="text-xs text-gray-600">R:R {trade.rr_achieved}:1</span>}
        </div>
        <div className="flex items-center gap-2">
          <PLBadge value={pl} />
          <span className="text-xs text-gray-600">{trade.confidence_score?.toFixed(0)}%</span>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-gray-800/50 px-3 py-2 text-xs" onClick={e => e.stopPropagation()}>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-2">
            <div><span className="text-gray-500">Entry:</span> <span className="font-mono text-gray-300">{trade.fill_price}</span></div>
            <div><span className="text-gray-500">Exit:</span> <span className="font-mono text-gray-300">{trade.close_price || 'Open'}</span></div>
            <div><span className="text-gray-500">SL:</span> <span className="font-mono text-red-400">{trade.stop_loss}</span></div>
            <div><span className="text-gray-500">TP:</span> <span className="font-mono text-green-400">{trade.take_profit}</span></div>
          </div>

          {trade.close_reason && (
            <div className="mb-2">
              <span className="text-gray-500">Closed:</span> <span className="text-gray-400">{trade.close_reason}</span>
            </div>
          )}

          {trade.breakdown && typeof trade.breakdown === 'object' && (
            <div className="flex flex-wrap gap-1 mb-2">
              {Object.entries(trade.breakdown).map(([k, v]) => (
                <span key={k} className="bg-gray-800 px-1.5 py-0.5 rounded text-[10px]">
                  <span className="text-gray-500">{k}:</span> <span className="text-gray-300 font-mono">{typeof v === 'number' ? v.toFixed(1) : v}</span>
                </span>
              ))}
            </div>
          )}

          {trade.reasoning && (
            <p className="text-gray-500 leading-relaxed">{trade.reasoning.substring(0, 300)}</p>
          )}
        </div>
      )}
    </div>
  )
}
