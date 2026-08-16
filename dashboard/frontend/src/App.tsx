import React, { useEffect, useState, useRef } from "react";
import {
  Activity,
  AlertTriangle,
  DollarSign,
  TrendingUp,
  TrendingDown,
  Shield,
  Power,
  RefreshCw,
  BarChart3,
  Newspaper,
  Layers,
  ToggleLeft,
  ToggleRight,
  Wifi,
  WifiOff,
  Crosshair,
  PieChart,
  Settings,
} from "lucide-react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";

interface RiskState {
  kill_switch: boolean;
  kill_reason?: string;
  risk_utilization_pct: number;
  max_drawdown_pct: number;
  max_daily_loss_usdc: number;
  max_position_pct_equity: number;
  max_leverage: number;
  max_open_positions: number;
}

interface Strategy {
  id: string;
  name: string;
  enabled: boolean;
  symbols: string[];
  timeframe: string;
  params: Record<string, any>;
  stats: { trades: number; win_rate: number; pnl: number };
}

interface Pair {
  symbol: string;
  active: boolean;
  type: string;
}

interface NewsItem {
  title: string;
  source: string;
  url: string;
  published: string;
  sentiment: string;
}

interface PortfolioState {
  equity: number;
  starting_equity: number;
  daily_pnl: number;
  weekly_pnl: number;
  unrealized_pnl: number;
  realized_pnl: number;
  available_balance: number;
  used_margin: number;
  mode: string;
  last_update: string;
  risk: RiskState;
  open_positions: any[];
  open_orders: any[];
}

interface EquityPoint {
  timestamp: string;
  equity: number;
}

interface Trade {
  id: string;
  symbol: string;
  side: string;
  quantity: number;
  entry_price: number;
  exit_price: number;
  net_pnl: number;
  opened_at?: string;
  closed_at?: string;
  strategy_id: string;
}

const API_BASE =
  window.location.hostname === "localhost"
    ? "http://localhost:8000"
    : `http://${window.location.hostname}:8000`;

const WS_URL =
  window.location.hostname === "localhost"
    ? "ws://localhost:8000/ws"
    : `ws://${window.location.hostname}:8000/ws`;

type Tab = "overview" | "analytics" | "strategies" | "pairs" | "risk" | "news";

export default function App() {
  const [state, setState] = useState<PortfolioState | null>(null);
  const [connected, setConnected] = useState(false);
  const [equityCurve, setEquityCurve] = useState<EquityPoint[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [pairs, setPairs] = useState<Pair[]>([]);
  const [news, setNews] = useState<NewsItem[]>([]);
  const [tab, setTab] = useState<Tab>("overview");
  const [loading, setLoading] = useState(true);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const [portfolio, curve, tradesRes, stratRes, pairsRes, newsRes] =
          await Promise.all([
            fetch(`${API_BASE}/api/portfolio`).then((r) => r.json()),
            fetch(`${API_BASE}/api/metrics/equity-curve?limit=500`).then((r) =>
              r.json()
            ),
            fetch(`${API_BASE}/api/trades?limit=50`).then((r) => r.json()),
            fetch(`${API_BASE}/api/strategies`).then((r) => r.json()),
            fetch(`${API_BASE}/api/pairs`).then((r) => r.json()),
            fetch(`${API_BASE}/api/news`).then((r) => r.json()),
          ]);
        setState(portfolio);
        setEquityCurve(curve.points || []);
        setTrades(tradesRes.trades || []);
        setStrategies(stratRes.strategies || []);
        setPairs(pairsRes.pairs || []);
        setNews(newsRes.news || []);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    };
    load();

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      ws.send(JSON.stringify({ action: "ping" }));
    };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "snapshot" || msg.type === "update") {
        const d = msg.data;
        setState((prev) => ({
          ...(prev || ({} as PortfolioState)),
          ...d,
          risk: d.risk || prev?.risk,
          open_positions: d.open_positions ?? prev?.open_positions,
          open_orders: d.open_orders ?? prev?.open_orders,
        }));
        if (d.equity_curve) setEquityCurve(d.equity_curve);
        if (d.trades) setTrades(d.trades);
        if (d.strategies) setStrategies(d.strategies);
        if (d.pairs) setPairs(d.pairs);
      }
    };
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);

    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: "ping" }));
      }
    }, 20000);

    return () => {
      clearInterval(ping);
      ws.close();
    };
  }, []);

  const toggleKillSwitch = async () => {
    if (!state) return;
    const next = !state.risk.kill_switch;
    await fetch(`${API_BASE}/api/risk/kill-switch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        active: next,
        reason: next ? "Activated from dashboard" : "Reset from dashboard",
      }),
    });
  };

  const toggleStrategy = async (id: string, enabled: boolean) => {
    await fetch(`${API_BASE}/api/strategies/${id}/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    setStrategies((prev) =>
      prev.map((s) => (s.id === id ? { ...s, enabled } : s))
    );
  };

  const togglePair = async (symbol: string, active: boolean) => {
    await fetch(`${API_BASE}/api/pairs/${symbol}/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active }),
    });
    setPairs((prev) =>
      prev.map((p) => (p.symbol === symbol ? { ...p, active } : p))
    );
  };

  if (loading || !state) {
    return (
      <div className="min-h-screen bg-slate-950 text-slate-100 flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <RefreshCw className="animate-spin w-8 h-8 text-emerald-400" />
          <p className="text-slate-400 text-sm">Connecting to trading engine…</p>
        </div>
      </div>
    );
  }

  const pnlPositive = state.daily_pnl >= 0;
  const totalReturn =
    ((state.equity - state.starting_equity) / state.starting_equity) * 100;

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "overview", label: "Overview", icon: <Activity className="w-4 h-4" /> },
    { id: "analytics", label: "Analytics", icon: <BarChart3 className="w-4 h-4" /> },
    { id: "strategies", label: "Strategies", icon: <Layers className="w-4 h-4" /> },
    { id: "pairs", label: "Pairs", icon: <Crosshair className="w-4 h-4" /> },
    { id: "risk", label: "Risk", icon: <Shield className="w-4 h-4" /> },
    { id: "news", label: "News", icon: <Newspaper className="w-4 h-4" /> },
  ];

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans">
      <header className="border-b border-slate-800 bg-slate-900/90 backdrop-blur sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Activity className="w-6 h-6 text-emerald-400" />
            <h1 className="text-lg font-semibold tracking-tight">Algo Trading Bot</h1>
            <span
              className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                state.mode === "paper"
                  ? "bg-amber-500/20 text-amber-300"
                  : "bg-rose-500/20 text-rose-300"
              }`}
            >
              {state.mode.toUpperCase()}
            </span>
          </div>
          <div className="flex items-center gap-4 text-sm">
            <div className="flex items-center gap-1.5">
              {connected ? (
                <Wifi className="w-4 h-4 text-emerald-400" />
              ) : (
                <WifiOff className="w-4 h-4 text-rose-400" />
              )}
              <span className={connected ? "text-emerald-400" : "text-rose-400"}>
                {connected ? "Live" : "Disconnected"}
              </span>
            </div>
            <span className="text-slate-500 text-xs hidden sm:inline">
              {new Date(state.last_update).toLocaleTimeString()}
            </span>
          </div>
        </div>

        <div className="max-w-7xl mx-auto px-4 flex gap-1 overflow-x-auto pb-2">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors whitespace-nowrap ${
                tab === t.id
                  ? "bg-emerald-500/15 text-emerald-400"
                  : "text-slate-400 hover:text-slate-200 hover:bg-slate-800"
              }`}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {tab === "overview" && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
              <KpiCard
                title="Equity"
                value={`$${state.equity.toLocaleString(undefined, {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}`}
                icon={<DollarSign className="w-4 h-4" />}
                accent="emerald"
              />
              <KpiCard
                title="Daily PnL"
                value={`${pnlPositive ? "+" : ""}${state.daily_pnl.toFixed(2)}`}
                icon={
                  pnlPositive ? (
                    <TrendingUp className="w-4 h-4" />
                  ) : (
                    <TrendingDown className="w-4 h-4" />
                  )
                }
                accent={pnlPositive ? "emerald" : "rose"}
              />
              <KpiCard
                title="Total Return"
                value={`${totalReturn >= 0 ? "+" : ""}${totalReturn.toFixed(2)}%`}
                icon={<PieChart className="w-4 h-4" />}
                accent={totalReturn >= 0 ? "emerald" : "rose"}
              />
              <KpiCard
                title="Risk Util."
                value={`${state.risk.risk_utilization_pct.toFixed(1)}%`}
                icon={<Shield className="w-4 h-4" />}
                accent="sky"
              />
              <KpiCard
                title="Max DD"
                value={`${(state.risk.max_drawdown_pct * 100).toFixed(2)}%`}
                icon={<AlertTriangle className="w-4 h-4" />}
                accent="amber"
              />
              <KpiCard
                title="Unrealized"
                value={`${
                  state.unrealized_pnl >= 0 ? "+" : ""
                }${state.unrealized_pnl.toFixed(2)}`}
                icon={<Activity className="w-4 h-4" />}
                accent={state.unrealized_pnl >= 0 ? "emerald" : "rose"}
              />
            </div>

            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
              <div>
                <h2 className="font-medium text-slate-200 mb-1">Risk Kill Switch</h2>
                <p className="text-sm text-slate-400">
                  Instantly halts all new orders. Existing positions remain open.
                  {state.risk.kill_reason && (
                    <span className="block mt-1 text-rose-400">
                      Reason: {state.risk.kill_reason}
                    </span>
                  )}
                </p>
              </div>
              <button
                onClick={toggleKillSwitch}
                className={`flex items-center gap-2 px-5 py-2.5 rounded-lg font-medium transition-all ${
                  state.risk.kill_switch
                    ? "bg-rose-600 hover:bg-rose-500 text-white shadow-lg shadow-rose-900/40"
                    : "bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700"
                }`}
              >
                <Power className="w-4 h-4" />
                {state.risk.kill_switch
                  ? "KILL SWITCH ACTIVE"
                  : "Activate Kill Switch"}
              </button>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <Panel title="Open Positions">
                {state.open_positions && state.open_positions.length > 0 ? (
                  <div className="space-y-2">
                    {state.open_positions.map((p: any, i: number) => (
                      <div
                        key={i}
                        className="flex justify-between items-center py-2 border-b border-slate-800 last:border-0"
                      >
                        <div>
                          <span className="font-medium text-slate-200">
                            {p.symbol}
                          </span>
                          <span
                            className={`ml-2 text-xs ${
                              p.side === "long"
                                ? "text-emerald-400"
                                : "text-rose-400"
                            }`}
                          >
                            {p.side?.toUpperCase()}
                          </span>
                        </div>
                        <div className="text-right text-sm">
                          <div>{Number(p.quantity).toFixed(4)}</div>
                          <div
                            className={
                              Number(p.unrealized_pnl) >= 0
                                ? "text-emerald-400"
                                : "text-rose-400"
                            }
                          >
                            {Number(p.unrealized_pnl).toFixed(2)} USDC
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <Empty text="No open positions" />
                )}
              </Panel>

              <Panel title="Open Orders">
                {state.open_orders && state.open_orders.length > 0 ? (
                  <pre className="text-xs overflow-auto text-slate-400">
                    {JSON.stringify(state.open_orders, null, 2)}
                  </pre>
                ) : (
                  <Empty text="No open orders" />
                )}
              </Panel>
            </div>
          </>
        )}

        {tab === "analytics" && (
          <>
            <Panel title="Equity Curve">
              <div className="h-72">
                {equityCurve.length > 1 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={equityCurve}>
                      <defs>
                        <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#34d399" stopOpacity={0.3} />
                          <stop offset="100%" stopColor="#34d399" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                      <XAxis
                        dataKey="timestamp"
                        tickFormatter={(v) => new Date(v).toLocaleTimeString()}
                        stroke="#64748b"
                        fontSize={11}
                      />
                      <YAxis
                        domain={["auto", "auto"]}
                        stroke="#64748b"
                        fontSize={11}
                        tickFormatter={(v) => `$${v}`}
                      />
                      <Tooltip
                        contentStyle={{
                          background: "#0f172a",
                          border: "1px solid #1e293b",
                          borderRadius: 8,
                        }}
                        labelFormatter={(v) => new Date(v).toLocaleString()}
                      />
                      <Area
                        type="monotone"
                        dataKey="equity"
                        stroke="#34d399"
                        fill="url(#eqGrad)"
                        strokeWidth={2}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                ) : (
                  <Empty text="Waiting for equity data…" />
                )}
              </div>
            </Panel>

            <Panel title="Trade Journal">
              {trades.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-slate-950/50 text-slate-400">
                      <tr>
                        <th className="text-left px-3 py-2 font-medium">Symbol</th>
                        <th className="text-left px-3 py-2 font-medium">Side</th>
                        <th className="text-right px-3 py-2 font-medium">Qty</th>
                        <th className="text-right px-3 py-2 font-medium">Entry</th>
                        <th className="text-right px-3 py-2 font-medium">Exit</th>
                        <th className="text-right px-3 py-2 font-medium">Net PnL</th>
                        <th className="text-left px-3 py-2 font-medium">Strategy</th>
                      </tr>
                    </thead>
                    <tbody>
                      {trades.map((t) => (
                        <tr
                          key={t.id}
                          className="border-t border-slate-800 hover:bg-slate-800/40"
                        >
                          <td className="px-3 py-2 font-medium text-slate-200">
                            {t.symbol}
                          </td>
                          <td
                            className={`px-3 py-2 ${
                              t.side === "long"
                                ? "text-emerald-400"
                                : "text-rose-400"
                            }`}
                          >
                            {t.side.toUpperCase()}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {t.quantity.toFixed(4)}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {t.entry_price.toFixed(2)}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {t.exit_price.toFixed(2)}
                          </td>
                          <td
                            className={`px-3 py-2 text-right font-medium ${
                              t.net_pnl >= 0 ? "text-emerald-400" : "text-rose-400"
                            }`}
                          >
                            {t.net_pnl >= 0 ? "+" : ""}
                            {t.net_pnl.toFixed(2)}
                          </td>
                          <td className="px-3 py-2 text-slate-400 text-xs">
                            {t.strategy_id}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Empty text="No closed trades yet" />
              )}
            </Panel>
          </>
        )}

        {tab === "strategies" && (
          <div className="space-y-4">
            {strategies.map((s) => (
              <div
                key={s.id}
                className="bg-slate-900 border border-slate-800 rounded-xl p-5"
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h3 className="font-medium text-slate-100 flex items-center gap-2">
                      {s.name}
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full ${
                          s.enabled
                            ? "bg-emerald-500/20 text-emerald-400"
                            : "bg-slate-700 text-slate-400"
                        }`}
                      >
                        {s.enabled ? "ACTIVE" : "OFF"}
                      </span>
                    </h3>
                    <p className="text-sm text-slate-400 mt-1">
                      {s.timeframe} · {s.symbols.join(", ")}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-3 text-xs text-slate-500">
                      {Object.entries(s.params).map(([k, v]) => (
                        <span key={k} className="bg-slate-800 px-2 py-1 rounded">
                          {k}: <span className="text-slate-300">{String(v)}</span>
                        </span>
                      ))}
                    </div>
                    <div className="mt-3 flex gap-4 text-sm">
                      <span>
                        Trades:{" "}
                        <strong className="text-slate-200">{s.stats.trades}</strong>
                      </span>
                      <span>
                        Win rate:{" "}
                        <strong className="text-slate-200">
                          {s.stats.win_rate.toFixed(1)}%
                        </strong>
                      </span>
                      <span>
                        PnL:{" "}
                        <strong
                          className={
                            s.stats.pnl >= 0 ? "text-emerald-400" : "text-rose-400"
                          }
                        >
                          {s.stats.pnl.toFixed(2)}
                        </strong>
                      </span>
                    </div>
                  </div>
                  <button
                    onClick={() => toggleStrategy(s.id, !s.enabled)}
                    className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium bg-slate-800 hover:bg-slate-700 border border-slate-700"
                  >
                    {s.enabled ? (
                      <>
                        <ToggleRight className="w-5 h-5 text-emerald-400" /> Disable
                      </>
                    ) : (
                      <>
                        <ToggleLeft className="w-5 h-5 text-slate-400" /> Enable
                      </>
                    )}
                  </button>
                </div>
              </div>
            ))}
            {strategies.length === 0 && <Empty text="No strategies loaded" />}
          </div>
        )}

        {tab === "pairs" && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-slate-800">
              <h2 className="font-medium">USDC Trading Pairs</h2>
              <p className="text-sm text-slate-400 mt-0.5">
                Toggle which pairs the bot is allowed to trade
              </p>
            </div>
            <div className="divide-y divide-slate-800">
              {pairs.map((p) => (
                <div
                  key={p.symbol}
                  className="flex items-center justify-between px-5 py-3 hover:bg-slate-800/40"
                >
                  <div className="flex items-center gap-3">
                    <span className="font-medium text-slate-200">{p.symbol}</span>
                    <span className="text-xs text-slate-500 uppercase">{p.type}</span>
                  </div>
                  <button
                    onClick={() => togglePair(p.symbol, !p.active)}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium ${
                      p.active
                        ? "bg-emerald-500/15 text-emerald-400"
                        : "bg-slate-800 text-slate-400"
                    }`}
                  >
                    {p.active ? (
                      <>
                        <ToggleRight className="w-4 h-4" /> Active
                      </>
                    ) : (
                      <>
                        <ToggleLeft className="w-4 h-4" /> Off
                      </>
                    )}
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {tab === "risk" && (
          <div className="space-y-6">
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <h2 className="font-medium mb-4 flex items-center gap-2">
                <Settings className="w-4 h-4 text-sky-400" />
                Risk Limits
              </h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <RiskStat
                  label="Max Daily Loss"
                  value={`${state.risk.max_daily_loss_usdc} USDC`}
                />
                <RiskStat
                  label="Max Position % Equity"
                  value={`${(state.risk.max_position_pct_equity * 100).toFixed(1)}%`}
                />
                <RiskStat label="Max Leverage" value={`${state.risk.max_leverage}x`} />
                <RiskStat
                  label="Max Open Positions"
                  value={`${state.risk.max_open_positions}`}
                />
                <RiskStat
                  label="Current Risk Utilization"
                  value={`${state.risk.risk_utilization_pct.toFixed(1)}%`}
                />
                <RiskStat
                  label="Max Drawdown"
                  value={`${(state.risk.max_drawdown_pct * 100).toFixed(2)}%`}
                />
              </div>
            </div>

            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
              <div>
                <h2 className="font-medium text-slate-200 mb-1">Kill Switch</h2>
                <p className="text-sm text-slate-400">
                  Emergency stop for all new order flow.
                </p>
              </div>
              <button
                onClick={toggleKillSwitch}
                className={`flex items-center gap-2 px-5 py-2.5 rounded-lg font-medium transition-all ${
                  state.risk.kill_switch
                    ? "bg-rose-600 hover:bg-rose-500 text-white"
                    : "bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700"
                }`}
              >
                <Power className="w-4 h-4" />
                {state.risk.kill_switch ? "ACTIVE – Click to Reset" : "Activate"}
              </button>
            </div>
          </div>
        )}

        {tab === "news" && (
          <div className="space-y-3">
            {news.map((n, i) => (
              <a
                key={i}
                href={n.url === "#" ? undefined : n.url}
                target="_blank"
                rel="noreferrer"
                className="block bg-slate-900 border border-slate-800 rounded-xl p-4 hover:border-slate-700 transition-colors"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="font-medium text-slate-100 leading-snug">
                      {n.title}
                    </h3>
                    <p className="text-xs text-slate-500 mt-1.5">
                      {n.source} · {new Date(n.published).toLocaleString()}
                    </p>
                  </div>
                  <span
                    className={`text-xs px-2 py-0.5 rounded-full shrink-0 ${
                      n.sentiment === "bullish"
                        ? "bg-emerald-500/20 text-emerald-400"
                        : n.sentiment === "bearish"
                        ? "bg-rose-500/20 text-rose-400"
                        : "bg-slate-700 text-slate-400"
                    }`}
                  >
                    {n.sentiment}
                  </span>
                </div>
              </a>
            ))}
            {news.length === 0 && <Empty text="No news available" />}
            <p className="text-xs text-slate-500 text-center pt-2">
              Demo news feed. Connect CryptoPanic / NewsAPI for live data.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}

function KpiCard({
  title,
  value,
  icon,
  accent,
}: {
  title: string;
  value: string;
  icon: React.ReactNode;
  accent: "emerald" | "rose" | "sky" | "amber";
}) {
  const colors = {
    emerald: "text-emerald-400 bg-emerald-400/10",
    rose: "text-rose-400 bg-rose-400/10",
    sky: "text-sky-400 bg-sky-400/10",
    amber: "text-amber-400 bg-amber-400/10",
  };
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-slate-400">{title}</span>
        <div className={`p-1.5 rounded-md ${colors[accent]}`}>{icon}</div>
      </div>
      <div className="text-lg font-semibold tracking-tight truncate">{value}</div>
    </div>
  );
}

function Panel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
      <div className="px-5 py-4 border-b border-slate-800">
        <h2 className="font-medium">{title}</h2>
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="py-8 text-center text-sm text-slate-500">{text}</div>;
}

function RiskStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-slate-950/50 rounded-lg px-4 py-3">
      <div className="text-xs text-slate-400 mb-1">{label}</div>
      <div className="font-medium text-slate-200">{value}</div>
    </div>
  );
}
