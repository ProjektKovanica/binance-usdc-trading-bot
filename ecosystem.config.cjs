module.exports = {
  apps: [
    {
      name: "trading-engine",
      cwd: "/root/trading-bot",
      script: "scripts/run_paper.py",
      interpreter: "/root/trading-bot/.venv/bin/python",
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      env: {
        PYTHONUNBUFFERED: "1"
      }
    },
    {
      name: "dashboard-api",
      cwd: "/root/trading-bot",
      script: "/root/trading-bot/.venv/bin/uvicorn",
      args: "dashboard.backend.main:app --host 0.0.0.0 --port 8000",
      interpreter: "none",
      autorestart: true
    },
    {
      name: "dashboard-ui",
      cwd: "/root/trading-bot/dashboard/frontend",
      script: "npm",
      args: "run dev -- --host 0.0.0.0 --port 5173",
      interpreter: "none",
      autorestart: true
    }
  ]
};
