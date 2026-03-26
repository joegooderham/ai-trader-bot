import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Overview from './pages/Overview'
import Positions from './pages/Positions'
import TradeHistory from './pages/TradeHistory'
import Summary from './pages/Summary'
import Analytics from './pages/Analytics'
import Wiki from './pages/Wiki'
import WikiPage from './pages/WikiPage'
import Config from './pages/Config'
import Backlog from './pages/Backlog'
import ConfigEditor from './pages/ConfigEditor'
import Remediation from './pages/Remediation'
import Chat from './pages/Chat'
import TradeJournal from './pages/TradeJournal'
import Heatmap from './pages/Heatmap'
import SessionAnalysis from './pages/SessionAnalysis'
import WhatIf from './pages/WhatIf'
import Correlations from './pages/Correlations'
import RiskExposure from './pages/RiskExposure'
import ScanLog from './pages/ScanLog'
import TradingGame from './pages/TradingGame'
import Calendar from './pages/Calendar'
import Benchmark from './pages/Benchmark'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Overview />} />
        <Route path="/positions" element={<Positions />} />
        <Route path="/trades" element={<TradeHistory />} />
        <Route path="/journal" element={<TradeJournal />} />
        <Route path="/scan-log" element={<ScanLog />} />
        <Route path="/calendar" element={<Calendar />} />
        <Route path="/benchmark" element={<Benchmark />} />
        <Route path="/summary" element={<Summary />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/heatmap" element={<Heatmap />} />
        <Route path="/sessions" element={<SessionAnalysis />} />
        <Route path="/correlations" element={<Correlations />} />
        <Route path="/risk" element={<RiskExposure />} />
        <Route path="/what-if" element={<WhatIf />} />
        <Route path="/game" element={<TradingGame />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/remediation" element={<Remediation />} />
        <Route path="/config" element={<ConfigEditor />} />
        <Route path="/config-readonly" element={<Config />} />
        <Route path="/wiki" element={<Wiki />} />
        <Route path="/wiki/:pageName" element={<WikiPage />} />
        <Route path="/backlog" element={<Backlog />} />
      </Routes>
    </Layout>
  )
}
