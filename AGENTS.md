## AGENTS — Project map (ai-mcp-autonomous-traders)

This document is a **contributor‑oriented map** of the repo: where key modules live, what each one does, and the main run commands.

---

## Repository layout

```text
ai-mcp-autonomous-traders/
├── README.md              # User-facing overview, setup, run instructions
├── AGENTS.md              # This file (contributor map)
├── .env                   # Local environment configuration (not committed)
├── docs/
│   ├── HLD.md             # High-level architecture & diagrams
│   ├── LLD.md             # Low-level design & call flows
│   └── developers_guide.md# Deeper developer notes & patterns
└── src/                   # Application code
    ├── app.py             # Gradio web UI (dashboard)
    ├── trading_floor.py   # Orchestrator: runs trader agents on a schedule
    ├── traders.py         # Trader + researcher agents and their wiring
    ├── templates.py       # Prompt templates and message formats
    ├── accounts.py        # Account model, PnL, positions, order logic
    ├── accounts_client.py # Thin client helpers for accounts DB
    ├── market.py          # Market data access (Polygon & fallbacks)
    ├── market_server.py   # MCP server exposing market tools
    ├── accounts_server.py # MCP server exposing account tools/resources
    ├── push_server.py     # MCP server for Pushover notifications
    ├── mcp_params.py      # How MCP servers are configured/launched
    ├── database.py        # SQLite persistence (accounts, logs, cache)
    ├── tracers.py         # Tracing/log helpers for agent runs
    ├── util.py            # Shared utility functions
    └── reset.py           # Reset accounts & strategies for all traders
```

If you only remember one thing: **`src/` holds the runtime, `docs/` explains the design, `README.md` explains how to run it.**

---

## Where to start reading

- **High-level overview**: `README.md`
- **Architecture & flows**: `docs/HLD.md`, `docs/LLD.md`
- **Runtime entrypoints**:
  - `src/trading_floor.py` – background orchestrator that schedules and runs trader agents.
  - `src/app.py` – Gradio dashboard for monitoring portfolios, trades, and logs.
- **Agents & prompts**:
  - `src/traders.py` – defines trader and researcher agents, wiring to MCP tools and LLMs.
  - `src/templates.py` – prompt templates and trade/rebalance messages.
- **Domain & persistence**:
  - `src/accounts.py`, `src/market.py`, `src/database.py` – accounts, market data, and SQLite storage.
- **MCP integration**:
  - `src/accounts_server.py`, `src/market_server.py`, `src/push_server.py`, `src/mcp_params.py`.

---

## Common dev/run commands

All commands assume you are in the repo root unless noted.

- **Install dependencies**

  ```bash
  uv sync
  ```

  (Or create a virtualenv and install requirements as described in `README.md`.)

- **Initialize / reset trader accounts**

  ```bash
  cd src
  uv run reset.py
  ```

- **Run the trading orchestrator (agents)**

  ```bash
  cd src
  uv run trading_floor.py
  ```

- **Run the web UI (dashboard)**

  ```bash
  cd src
  uv run app.py
  ```

For more details (env vars, MCP servers, supported LLMs), see **`README.md`** and **`docs/developers_guide.md`**.

