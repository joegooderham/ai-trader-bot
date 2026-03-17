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

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Overview />} />
        <Route path="/positions" element={<Positions />} />
        <Route path="/trades" element={<TradeHistory />} />
        <Route path="/summary" element={<Summary />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/wiki" element={<Wiki />} />
        <Route path="/wiki/:pageName" element={<WikiPage />} />
        <Route path="/backlog" element={<Backlog />} />
        <Route path="/config" element={<Config />} />
      </Routes>
    </Layout>
  )
}
