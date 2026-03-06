# Code Flow & Dependencies — From Scratch to Runtime

This document explains the project from the ground up: build order, prerequisites, what each component does, and how they combine into `traders.py` and `trading_floor.py`.

---

## 1. Build Order (Prerequisites)

Components are built in layers. Each layer depends on the ones below it.

```
Layer 1: database.py          ← SQLite schema & persistence
Layer 2: market.py            ← Market data (Polygon API + DB cache)
Layer 3: accounts.py          ← Account model & trading logic
Layer 4: reset.py             ← Seed trader accounts & strategies
Layer 5: MCP servers          ← accounts_server, push_server, market_server
Layer 6: mcp_params.py        ← MCP server configuration
Layer 7: accounts_client.py   ← Client to read from accounts MCP
Layer 8: templates, util, tracers
Layer 9: traders.py           ← Trader agent orchestration
Layer 10: trading_floor.py    ← Scheduler that runs all traders
Layer 11: app.py              ← Gradio dashboard (reads DB directly)
```

---

## 2. Layer Details

### Layer 1: `database.py`

**Role:** SQLite persistence and schema.

**What it does:**
- Creates `accounts.db` with tables:
  - `accounts` (name, account)
  - `logs` (id, name, datetime, type, message)
  - `market` (date, data)
- Exposes:
  - `write_account(name, account_dict)` — JSON-serializes account and upserts
  - `read_account(name)` — reads account JSON
  - `write_log`, `read_log` — log entries
  - `write_market`, `read_market` — market data cache

**Dependencies:** None (pure SQLite + json).

---

### Layer 2: `market.py`

**Role:** Stock price data.

**What it does:**
- Uses Polygon API if `POLYGON_API_KEY` is set:
  - EOD: `get_share_price_polygon_eod` — previous close
  - Paid/realtime: `get_share_price_polygon_min` — snapshot
- Caches market data in `database.write_market` / `read_market`
- Falls back to random price if Polygon fails
- Exports `is_paid_polygon`, `is_realtime_polygon` for MCP config

**Dependencies:** `database` (write_market, read_market).

---

### Layer 3: `accounts.py`

**Role:** Account model and trading logic.

**What it does:**
- `Transaction` — Pydantic model for a single trade
- `Account` — Pydantic model with:
  - `name`, `balance`, `strategy`, `holdings`, `transactions`, `portfolio_value_time_series`
  - `Account.get(name)` — load from DB or create new
  - `save()` — `write_account(self.name, self.model_dump())`
  - `reset(strategy)` — reset balance, strategy, holdings; save
  - `buy_shares`, `sell_shares` — use `market.get_share_price`, update holdings, `save()`, `write_log`
  - `report()`, `get_strategy()`, `change_strategy()`

**Dependencies:** `database`, `market`.

---

### Layer 4: `reset.py`

**Role:** Seed the four traders with strategies.

**What it does:**
- Defines long-form strategy strings: `waren_strategy`, `george_strategy`, `ray_strategy`, `cathie_strategy`
- `reset_traders()` calls `Account.get("Warren").reset(waren_strategy)` (and same for George, Ray, Cathie)
- Each `reset()` writes strategy into `accounts.db` via `Account.save()` → `database.write_account()`

**Dependencies:** `accounts`.

**When to run:** Before first use, or to reset all traders to a clean state.

---

### Layer 5: MCP Servers (Internal)

These are **stdio MCP servers** that the orchestrator spawns as child processes.

#### `accounts_server.py`

**Role:** Expose account operations as MCP tools and resources.

**What it does:**
- Uses FastMCP; runs `uv run accounts_server.py` over stdio
- **Tools:** `get_balance`, `get_holdings`, `buy_shares`, `sell_shares`, `change_strategy`
- **Resources:**
  - `accounts://accounts_server/{name}` — full account JSON (via `Account.report()`)
  - `accounts://strategy/{name}` — strategy text (via `Account.get_strategy()`)
- All tools/resources call `Account.get(name)` and delegate to `accounts.py`

**Dependencies:** `accounts` (which uses `database`, `market`).

---

#### `push_server.py`

**Role:** Send push notifications via Pushover API.

**What it does:**
- Tool: `push(message)` — POSTs to `https://api.pushover.net/1/messages.json`
- Uses `PUSHOVER_USER`, `PUSHOVER_TOKEN` from env

**Dependencies:** `requests`, env vars.

---

#### `market_server.py`

**Role:** Expose market data as an MCP tool.

**What it does:**
- Tool: `lookup_share_price(symbol)` — calls `market.get_share_price(symbol)`

**Dependencies:** `market`.

---

### Layer 6: `mcp_params.py`

**Role:** Define *how* to start each MCP server (command, args, env).

**What it does:**
- **Trader MCP servers** (used by the Trader agent):
  - `accounts_server.py` — internal
  - `push_server.py` — internal
  - **Market:** if `POLYGON_PLAN` is `paid` or `realtime` → external `uvx mcp_polygon`; else → internal `market_server.py`
- **Researcher MCP servers** (used by the Researcher agent):
  - `uvx mcp-server-fetch` — HTTP fetch
  - `npx @modelcontextprotocol/server-brave-search` — Brave Search API
  - `npx mcp-memory-libsql` — per-trader memory DB at `./memory/{name}.db`

**Dependencies:** `market` (for `is_paid_polygon`, `is_realtime_polygon`).

---

### Layer 7: `accounts_client.py`

**Role:** Client-side helper to read account and strategy from the accounts MCP server.

**What it does:**
- Spawns `uv run accounts_server.py` over stdio
- `read_accounts_resource(name)` — reads `accounts://accounts_server/{name}` → full account JSON
- `read_strategy_resource(name)` — reads `accounts://strategy/{name}` → strategy string
- Used by `traders.py` *before* the agent runs, to build the initial message

**Note:** This starts its *own* accounts_server process. The Trader agent also gets an accounts MCP server in `trader_mcp_server_params`; that one is used during the conversation for tool calls (buy/sell, etc.).

**Dependencies:** `mcp`, `StdioServerParameters`.

---

### Layer 8: `templates.py`, `util.py`, `tracers.py`

- **templates.py:** Prompt text for Researcher, Trader, trade/rebalance messages. Uses `market` for `is_paid_polygon` / `is_realtime_polygon` to tailor instructions.
- **util.py:** CSS/JS and `Color` enum for the Gradio UI.
- **tracers.py:** `make_trace_id`, `LogTracer` — writes trace/span events to `database.write_log` for the dashboard.

---

### Layer 9: `traders.py`

**Role:** Orchestrate a single trader’s run: MCP servers → agents → Runner.

**What it does:**
- **Step 0:** Create MCP servers from `mcp_params`:
  - Trader MCP: accounts, push, market
  - Researcher MCP: fetch, Brave, memory
- **Steps 1–5:**
  1. Build Researcher agent (with researcher MCP servers)
  2. Wrap Researcher as a Tool
  3. Build Trader agent (with Researcher tool + trader MCP servers)
  4. Load account + strategy via `accounts_client.read_accounts_resource`, `read_strategy_resource`
  5. Build message (trade or rebalance), call `Runner.run(agent, message)`

**Dependencies:**
- `accounts_client` — for account + strategy before agent run
- `mcp_params` — for server config
- `templates` — prompts
- `tracers` — trace IDs
- `agents` — Agent, Tool, Runner, MCPServerStdio

**Flow:** `Trader.run()` → `run_with_trace()` → `run_with_mcp_servers()` → `run_agent()` → `Runner.run()`.

---

### Layer 10: `trading_floor.py`

**Role:** Scheduler that runs all four traders periodically.

**What it does:**
- Creates `Trader("Warren", "Patience", ...)`, etc.
- Every `RUN_EVERY_N_MINUTES` minutes:
  - If market is open (or `RUN_EVEN_WHEN_MARKET_IS_CLOSED`):
    - `asyncio.gather(*[trader.run() for trader in traders])`
  - Else: skip and sleep
- Registers `LogTracer` to write trace events to DB

**Dependencies:** `traders`, `market` (is_market_open), `tracers`, `agents`.

---

### Layer 11: `app.py`

**Role:** Gradio dashboard.

**What it does:**
- Reads `Account.get(name)` directly from `accounts.py` (no MCP)
- Reads `database.read_log` for activity logs
- Uses `util` for styling
- Displays portfolio value, holdings, transactions, charts

**Dependencies:** `accounts`, `database`, `util`, `trading_floor` (names, lastnames, short_model_names).

---

## 3. Dependency Diagram

```
                    database.py
                         │
         ┌───────────────┼───────────────┐
         │               │               │
      market.py      accounts.py      tracers.py
         │               │               │
         │         ┌────┴────┐          │
         │         │         │          │
         │    reset.py  accounts_server  │
         │         │         │          │
         │         │    push_server     │
         │         │         │          │
         │    market_server  │          │
         │         │         │          │
         └─────────┼─────────┼──────────┘
                   │         │
              mcp_params  accounts_client
                   │         │
                   └────┬────┘
                        │
                   templates.py
                        │
                   traders.py
                        │
                 trading_floor.py
                        │
                    app.py
```

---

## 4. End-to-End Code Flow

### 4.1 Setup (one-time)

```
1. uv sync
2. .env with API keys (Polygon, OpenRouter/LLM, Brave, Pushover)
3. cd src && uv run reset.py   → seeds Warren, George, Ray, Cathie in accounts.db
```

### 4.2 Runtime

```
trading_floor.py (main loop)
    │
    ├─ every RUN_EVERY_N_MINUTES:
    │     if market open (or override):
    │         asyncio.gather(trader.run() for each trader)
    │
    └─ Trader.run()
          ├─ run_with_trace()
          │     └─ run_with_mcp_servers()
          │           ├─ Create trader MCP servers (accounts, push, market)
          │           ├─ Create researcher MCP servers (fetch, Brave, memory)
          │           └─ run_agent(trader_mcp_servers, researcher_mcp_servers)
          │                 ├─ create_agent()       → Researcher tool + Trader agent
          │                 ├─ get_account_report() → accounts_client.read_accounts_resource
          │                 ├─ read_strategy_resource
          │                 ├─ Build message (trade_message or rebalance_message)
          │                 └─ Runner.run(agent, message)
          │                       └─ LLM + tools loop:
          │                             - Trader calls Researcher tool
          │                             - Trader calls accounts MCP (buy/sell)
          │                             - Trader calls push MCP (notification)
          │                             - Trader calls market MCP (prices)
          └─ Toggle do_trade (trade ↔ rebalance next run)
```

### 4.3 Dashboard (parallel)

```
app.py
    └─ Gradio UI
          ├─ Account.get(name) for each trader
          ├─ read_log(name) for activity
          └─ Renders portfolio value, holdings, transactions, charts
```

---

## 5. Internal vs External MCP

| Server | Type | What it does |
|--------|------|--------------|
| `accounts_server.py` | Internal | Tools: get_balance, get_holdings, buy_shares, sell_shares, change_strategy. Resources: account JSON, strategy. Backed by `accounts.db`. |
| `push_server.py` | Internal | Tool: push. Calls Pushover API. |
| `market_server.py` | Internal | Tool: lookup_share_price. Uses `market.get_share_price`. |
| `mcp_polygon` (uvx) | External | Polygon.io MCP; used when POLYGON_PLAN is paid/realtime. |
| `mcp-server-fetch` (uvx) | External | Generic HTTP fetch. |
| `@modelcontextprotocol/server-brave-search` (npx) | External | Brave Search API. |
| `mcp-memory-libsql` (npx) | External | Per-trader SQLite memory at `./memory/{name}.db`. |

---

## 6. Summary for a Fresh Reader

1. **database.py** — SQLite schema and persistence.
2. **market.py** — Stock prices (Polygon or fallback).
3. **accounts.py** — Account model; buy/sell logic; reads/writes DB.
4. **reset.py** — Seeds four traders with strategies into DB.
5. **accounts_server, push_server, market_server** — MCP servers that expose tools/resources.
6. **mcp_params.py** — Config for which MCP servers to start and how.
7. **accounts_client** — Reads account/resource from accounts MCP before agent run.
8. **traders.py** — Builds Trader + Researcher agents, wires MCP servers, runs `Runner.run()`.
9. **trading_floor.py** — Scheduler; runs all traders every N minutes.
10. **app.py** — Dashboard that reads DB; no MCP.

**Flow:** `reset.py` seeds DB → `trading_floor.py` runs traders → each `Trader.run()` starts MCP servers, builds agents, runs conversation → agents use MCP tools to research, trade, push → `app.py` reads DB and shows results.
