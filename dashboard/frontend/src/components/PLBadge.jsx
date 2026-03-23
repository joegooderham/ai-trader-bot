/**
 * Coloured P&L badge — green for profit, red for loss.
 */
export default function PLBadge({ value, prefix = '£' }) {
  const isProfit = value >= 0
  const color = isProfit ? 'text-profit' : 'text-loss'
  const sign = isProfit ? '+' : '-'

  return (
    <span className={`font-mono font-semibold ${color}`}>
      {sign}{prefix}{Math.abs(value).toFixed(2)}
    </span>
  )
}
