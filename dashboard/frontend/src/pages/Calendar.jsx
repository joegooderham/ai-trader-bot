import { useState } from 'react'
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
  const { data } = useApi('/api/calendar?months=6')

  const calendarData = data || {}

  function prevMonth() {
    if (month === 0) { setMonth(11); setYear(y => y - 1) }
    else setMonth(m => m - 1)
  }

  function nextMonth() {
    if (month === 11) { setMonth(0); setYear(y => y + 1) }
    else setMonth(m => m + 1)
  }

  // Build calendar grid for current month
  const firstDay = new Date(year, month, 1)
  const lastDay = new Date(year, month + 1, 0)
  const startDow = (firstDay.getDay() + 6) % 7 // Monday = 0
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
      <p className="text-sm text-gray-500 mb-6">Daily trading performance at a glance.</p>

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
        {/* Day headers */}
        <div className="grid grid-cols-7 border-b border-gray-800">
          {DAYS.map(d => (
            <div key={d} className="text-center text-xs text-gray-500 py-2 font-medium">{d}</div>
          ))}
        </div>

        {/* Calendar cells */}
        <div className="grid grid-cols-7">
          {/* Empty cells before first day */}
          {Array.from({ length: startDow }, (_, i) => (
            <div key={`empty-${i}`} className="border-b border-r border-gray-800/50 min-h-[80px] bg-gray-950/30" />
          ))}

          {/* Day cells */}
          {Array.from({ length: daysInMonth }, (_, i) => {
            const d = i + 1
            const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`
            const day = calendarData[dateStr]
            const isToday = dateStr === today.toISOString().substring(0, 10)
            const isWeekend = new Date(year, month, d).getDay() % 6 === 0

            return (
              <div
                key={d}
                className={`border-b border-r border-gray-800/50 min-h-[80px] p-1.5 ${
                  isToday ? 'bg-blue-900/20 border-blue-800/50' :
                  isWeekend ? 'bg-gray-950/50' : ''
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className={`text-xs font-medium ${isToday ? 'text-blue-400' : 'text-gray-500'}`}>
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

      {/* Legend */}
      <div className="flex items-center gap-4 mt-3 text-xs text-gray-500">
        <div className="flex items-center gap-1"><span className="text-green-400 font-bold">+£</span> Profitable day</div>
        <div className="flex items-center gap-1"><span className="text-red-400 font-bold">-£</span> Loss day</div>
        <div>T=trades W=wins L=losses BE=breakeven</div>
      </div>
    </div>
  )
}
