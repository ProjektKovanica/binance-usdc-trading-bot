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
  BookOpen,
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

interface PortfolioState {
  equity: number;
  daily_pnl: number;
  mode: string;
  last_update: string;
  risk: {
    kill_switch: boolean;
    risk_utilization_pct: number;
    max_drawdown_pct: number;
  };
  open_positions?: any[];
  open_orders?: any[];
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

const API_BASE = "http://localhost:8000";
const WS_URL = "ws://localhost:8000/ws";

export default function App() {
  const [state, setState] = useState<PortfolioState | null>(null);
  const [connected, setConnected] = useState(false);
  const [equityCurve, setEquityCurve] = useState<EquityPoint[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/portfolio`)
      .then((r) => r.json())
      .then(setState)
      .catch(console.error);

    fetch(`${API_BASE}/api/metrics/equity-curve?limit=500`)
      .then((r) => r.json())
      .then((d) => setEquityCurve(d.points || []))
      .catch(() => {});

    fetch(`${API_BASE}/api/trades?limit=50`)
      .then((r) => r.json())
      .then((d) => setTrades(d.trades || []))
      .catch(() => {});

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      ws.send(JSON.stringify({ action: "ping" }));
    };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "snapshot" || msg.type === "update") {
        setState(msg.data);
        if (msg.data.equity_curve) setEquityCurve(msg.data.equity_curve);
        if (msg.data.trades) setTrades(msg.data.trades);
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

  if (!state) {
    return (
      <div className="min-h-screen bg-slate-950 text-slate-100 flex items-center justify-center">
        <RefreshCw className="animate-spin w-8 h-8 text-emerald-400" />
      </div>
    );
  }

  const pnlPositive = state.daily_pnl >= 0;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans">
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-50">
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
              <div
                className={`w-2 h-2 rounded-full ${
                  connected ? "bg-emerald-400 animate-pulse" : "bg-rose-500"
                }`}
              />
              {connected ? "Live" : "Disconnected"}
            </div>
            <span className="text-slate-500 text-xs">
              {new Date(state.last_update).toLocaleTimeString()}
            </span>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {/* KPI cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <KpiCard
            title="Equity"
            value={`$${state.equity.toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}`}
            icon={<DollarSign className="w-5 h-5" />}
            accent="emerald"
          />
          <KpiCard
            title="Daily PnL"
            value={`${pnlPositive ? "+" : ""}${state.daily_pnl.toFixed(2)} USDC`}
            icon={pnlPositive ? <TrendingUp className="w-5 h-5" /> : <TrendingDown className="w-5 h-5" />}
            accent={pnlPositive ? "emerald" : "rose"}
          />
          <KpiCard
            title="Risk Utilization"
            value={`${state.risk.risk_utilization_pct.toFixed(1)}%`}
            icon={<Shield className="w-5 h-5" />}
            accent="sky"
          />
          <KpiCard
            title="Max Drawdown"
            value={`${(state.risk.max_drawdown_pct * 100).toFixed(2)}%`}
            icon={<AlertTriangle className="w-5 h-5" />}
            accent="amber"
          />
        </div>

        {/* Kill switch */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div>
            <h2 className="font-medium text-slate-200 mb-1">Risk Kill Switch</h2>
            <p className="text-sm text-slate-400">
              Instantly halts all new orders. Existing positions remain open.
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
            {state.risk.kill_switch ? "KILL SWITCH ACTIVE" : "Activate Kill Switch"}
          </button>
        </div>

        {/* Equity curve */}
        <section className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-slate-800 flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-emerald-400" />
            <h2 className="font-medium">Equity Curve</h2>
          </div>
          <div className="p-4 h-72">
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
              <div className="h-full flex items-center justify-center text-slate-500 text-sm">
                Waiting for equity data…
              </div>
            )}
          </div>
        </section>

        {/* Positions + Orders */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <section className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-slate-800">
              <h2 className="font-medium">Open Positions</h2>
            </div>
            <div className="p-5 text-sm text-slate-400">
              {state.open_positions && state.open_positions.length > 0 ? (
                <div className="space-y-2">
                  {state.open_positions.map((p: any, i: number) => (
                    <div key={i} className="flex justify-between items-center py-2 border-b border-slate-800 last:border-0">
                      <div>
                        <span className="font-medium text-slate-200">{p.symbol}</span>
                        <span className={`ml-2 text-xs ${p.side === "long" ? "text-emerald-400" : "text-rose-400"}`}>
                          {p.side?.toUpperCase()}
                        </span>
                      </div>
                      <div className="text-right">
                        <div>{Number(p.quantity).toFixed(4)}</div>
                        <div className={Number(p.unrealized_pnl) >= 0 ? "text-emerald-400" : "text-rose-400"}>
                          {Number(p.unrealized_pnl).toFixed(2)} USDC
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                "No open positions"
              )}
            </div>
          </section>

          <section className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-slate-800">
              <h2 className="font-medium">Open Orders</h2>
            </div>
            <div className="p-5 text-sm text-slate-500">
              {state.open_orders && state.open_orders.length > 0 ? (
                <pre className="text-xs overflow-auto">{JSON.stringify(state.open_orders, null, 2)}</pre>
              ) : (
                "No open orders"
              )}
            </div>
          </section>
        </div>

        {/* Trade journal */}
        <section className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-slate-800 flex items-center gap-2">
            <BookOpen className="w-4 h-4 text-sky-400" />
            <h2 className="font-medium">Trade Journal</h2>
          </div>
          <div className="overflow-x-auto">
            {trades.length > 0 ? (
              <table className="w-full text-sm">
                <thead className="bg-slate-950/50 text-slate-400">
                  <tr>
                    <th className="text-left px-4 py-3 font-medium">Symbol</th>
                    <th className="text-left px-4 py-3 font-medium">Side</th>
                    <th className="text-right px-4 py-3 font-medium">Qty</th>
                    <th className="text-right px-4 py-3 font-medium">Entry</th>
                    <th className="text-right px-4 py-3 font-medium">Exit</th>
                    <th className="text-right px-4 py-3 font-medium">Net PnL</th>
                    <th className="text-left px-4 py-3 font-medium">Strategy</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t) => (
                    <tr key={t.id} className="border-t border-slate-800 hover:bg-slate-800/40">
                      <td className="px-4 py-2.5 font-medium text-slate-200">{t.symbol}</td>
                      <td className={`px-4 py-2.5 ${t.side === "long" ? "text-emerald-400" : "text-rose-400"}`}>
                        {t.side.toUpperCase()}
                      </td>
                      <td className="px-4 py-2.5 text-right">{t.quantity.toFixed(4)}</td>
                      <td className="px-4 py-2.5 text-right">{t.entry_price.toFixed(2)}</td>
                      <td className="px-4 py-2.5 text-right">{t.exit_price.toFixed(2)}</td>
                      <td className={`px-4 py-2.5 text-right font-medium ${t.net_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                        {t.net_pnl >= 0 ? "+" : ""}{t.net_pnl.toFixed(2)}
                      </td>
                      <td className="px-4 py-2.5 text-slate-400 text-xs">{t.strategy_id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="p-5 text-sm text-slate-500">No closed trades yet</div>
            )}
          </div>
        </section>
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
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-slate-400">{title}</span>
        <div className={`p-2 rounded-lg ${colors[accent]}`}>{icon}</div>
      </div>
      <div className="text-2xl font-semibold tracking-tight">{value}</div>
    </div>
  );
}
