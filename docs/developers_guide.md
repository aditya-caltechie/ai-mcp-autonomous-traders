## Developers Guide – AI MCP Autonomous Traders

Step‑by‑step guide to understand, extend, or build a similar **MCP‑based autonomous trading system** from scratch.

For architectural context, see `docs/architrcture.md` and `docs/LLD.md`. For a reference style of dev guide, you can also look at the external [Engineering Team — Developers Guide](https://github.com/aditya-caltechie/ai-crew-engineering-team/blob/main/docs/developers_guide.md).

---

## 1. Recommended Implementation Order

When building or extending this project (or a similar one), follow this order:

```text
1. Domain & Persistence      → accounts, market data, database
2. External Integrations     → Polygon, LLM providers, Pushover, Brave
3. MCP Servers               → accounts_server, market_server, push_server, external MCPs
4. Agents & Prompts          → Trader & Researcher agents, templates
5. Orchestrator              → trading_floor (scheduler)
6. Web UI                    → Gradio app (app.py)
7. Observability & Testing   → tracers, logs, basic tests
```

### High‑Level Build Flow

```mermaid
flowchart LR
    subgraph Step1["Step 1: Domain & DB"]
        D1[accounts.py\nTransaction & Account models]
        D2[market.py\nPrice providers]
        D3[database.py\nSQLite schema & helpers]
    end

    subgraph Step2["Step 2: External APIs"]
        E1[Polygon.io]
        E2[LLM APIs\n(OpenRouter, DeepSeek, Grok, Gemini)]
        E3[Pushover]
        E4[Brave Search]
    end

    subgraph Step3["Step 3: MCP Servers"]
        M1[accounts_server.py]
        M2[market_server.py / mcp_polygon]
        M3[push_server.py]
        M4[External MCPs\n(fetch, Brave, memory)]
    end

    subgraph Step4["Step 4: Agents & Prompts"]
        A1[Trader agent\ntraders.py]
        A2[Researcher agent\ntraders.py + templates.py]
        A3[mcp_params.py\nMCP wiring]
    end

    subgraph Step5["Step 5: Orchestrator & UI"]
        O1[trading_floor.py\nscheduler]
        U1[app.py\nGradio dashboard]
    end

    Step1 --> Step2 --> Step3 --> Step4 --> Step5
```

---

## 2. Step‑by‑Step: Building from Scratch

### Step 1: Clarify Requirements & Scenarios

- **Define trader personas**:
  - Names, risk profiles, strategies (e.g. value, macro, systematic, innovation/crypto).
- **Decide simulation vs. live trading**:
  - This project simulates trades in an internal ledger, not on a broker.
- **Decide market data source**:
  - Polygon EOD / realtime, or a mock/random generator for local dev.
- **Decide how you want to observe behavior**:
  - Logs, UI dashboard, push notifications, or all three.

Document these first; they drive the domain model, MCP tools, and prompts.

---

### Step 2: Design Domain & Data Model

**Goal:** Have a solid “engine” before adding MCP and agents.

- **Account model**
  - Fields to include:
    - `name`, `balance`, `strategy`, `holdings`, `transactions`, `portfolio_value_time_series`.
  - Behaviors:
    - `buy_shares`, `sell_shares`, `deposit`, `withdraw`, `reset`, `report`, `change_strategy`.
  - Persistence:
    - Read/write using a simple repository (`database.py`) before MCP exists.

- **Transaction model**
  - Fields:
    - `symbol`, `quantity`, `price`, `timestamp`, `rationale`.
  - Methods:
    - `total()` for cash impact, plus `__repr__` for logging.

- **Market data**
  - Decide primary function:
    - e.g. `get_share_price(symbol)`, plus internal helpers for EOD vs realtime.
  - Implement fallback behavior:
    - If API fails or key is missing, return a random price (useful in dev).

- **Database layer**
  - Choose a simple DB (SQLite is enough for this pattern).
  - Create helpers:
    - `write_account`, `read_account`
    - `write_log`, `read_log`
    - `write_market`, `read_market`
  - Initialize schema on import to keep setup easy.

Only when these work end‑to‑end (without MCP/agents) should you proceed.

---

### Step 3: Wire External Integrations

**Environment & configuration**

- Use `.env` + `python-dotenv` for:
  - Polygon, LLMs, Brave, Pushover, scheduler knobs.
- Keep keys **out of code**; reference only via `os.getenv(...)`.

**External providers**

- **Market data (Polygon.io)**:
  - Implement:
    - EOD path (`get_grouped_daily_aggs`) + local caching.
    - Realtime snapshot path (`get_snapshot_ticker`) when plan allows.
- **LLM providers**:
  - Wrap each provider in an `AsyncOpenAI` client with its base URL and key.
  - Implement a small router like `get_model(model_name)` that:
    - Interprets the string (contains `"deepseek"`, `"grok"`, `"gemini"`, or a `/` for OpenRouter).
    - Returns an `OpenAIChatCompletionsModel` pointing to the appropriate client.
- **Pushover**:
  - Simple POST with `user`, `token`, `message`.
  - Handle failure gracefully; a failed push should not crash the trader.
- **Brave Search**:
  - Handled via MCP server; main concern here is setting `BRAVE_API_KEY`.

Keep each integration **thin** and **testable** on its own.

---

### Step 4: Design MCP Servers & Tools

**Why MCP here?**

- Keeps agents decoupled from implementation details.
- Lets you mix local and external tools uniformly.

**Design guidelines**

- **Accounts MCP**:
  - Tools should mirror the domain surface you want the LLM to use:
    - `get_balance`, `get_holdings`, `buy_shares`, `sell_shares`, `change_strategy`.
  - Resources should provide **read‑only snapshots**:
    - `/accounts_server/{name}` → full account JSON.
    - `/strategy/{name}` → strategy text.
  - Keep tools idempotent or at least “obviously effectful” in their names.

- **Market MCP**:
  - Start with a single tool:
    - `lookup_share_price(symbol)` → float.
  - Later you can add:
    - OHLC series, technical indicators, fundamentals, etc.

- **Push MCP**:
  - Minimal; just enough for:
    - `push(message)` → "Push notification sent".

- **External MCP servers**:
  - `mcp-server-fetch`, Brave Search, and memory are “off‑the‑shelf”.
  - In `mcp_params.py`, only configure commands/env and treat them as black boxes.

**Implementation pattern**

- Use `FastMCP("service_name")` and decorate tools/resources.
- Keep domain logic **in domain modules**, not in MCP handlers.
- MCP handlers should be small adapters:
  - Deserialize args → call domain model → return primitive / JSONable result.

---

### Step 5: Build Agents & Prompts

**Trader agent**

- Responsibilities:
  - Understand its own strategy and current account.
  - Use Researcher + market/account tools to:
    - Research, then trade or rebalance.
  - Send a short push notification.
  - Summarize actions and outlook in natural language.

- Construction steps:
  1. **Create Researcher agent** (with fetch/Brave/memory MCPs).
  2. Turn Researcher into a `Tool` (`as_tool("Researcher", ...)`).
  3. Create Trader `Agent`:
     - Instructions from `templates.trader_instructions(name)`.
     - Tools: Researcher tool + MCP tool surfaces (accounts, market, push).
  4. Build the initial message:
     - Use `trade_message(...)` or `rebalance_message(...)` based on `do_trade` flag.

**Researcher agent**

- Instructions should emphasize:
  - Multiple searches, cross‑checking sources.
  - Use of memory tools to store/reuse knowledge.
  - Returning concise, decision‑ready summaries.

**Prompt design tips**

- Be explicit about:
  - Which tools to use for what (research vs market data vs trading vs strategy).
  - When to trade vs rebalance.
  - Risk behavior per persona (e.g., Warren vs Cathie).
- Keep prompts in **separate functions** (`templates.py`) so they’re easy to evolve.

---

### Step 6: Orchestrator & Scheduling

**Orchestrator (`trading_floor.py`)**

- Loads config from `.env`:
  - `RUN_EVERY_N_MINUTES`, `RUN_EVEN_WHEN_MARKET_IS_CLOSED`, `USE_MANY_MODELS`.
- Creates a list of `Trader` instances (names + model names).
- Installs `LogTracer` via `add_trace_processor(...)`.
- Main loop:
  - If allowed to run (market open or override):
    - `asyncio.gather(trader.run() for trader in traders)`.
  - Sleep for the configured number of minutes.

**Design tips**

- Keep the orchestrator **thin**:
  - It should schedule work, not contain trading logic.
- Handle exceptions:
  - Catch and log per‑trader errors so one failure doesn’t kill the loop.
- Use environment flags to:
  - Quickly switch between “demo mode” (always run) and “realistic mode” (respect market hours).

---

### Step 7: Web UI & Observability

**UI (`app.py`)**

- Only **reads** from domain & DB; it never calls MCP or LLMs.
- Per‑trader column shows:
  - Title (name, model, last name).
  - Portfolio value and PnL.
  - Chart of portfolio value over time.
  - Holdings and recent transactions.
  - Logs pulled from the `logs` table.

**Timers**

- Use a slower timer for **heavy** updates (portfolio, chart, holdings, transactions).
- Use a faster timer for **lightweight** updates (logs).

**Tracing (`tracers.py`)**

- Attach a `TracingProcessor` (e.g., `LogTracer`) that:
  - Maps spans and traces to trader names.
  - Writes structured logs into the DB.
- This gives you a timeline of what each trader and tool call is doing.

---

## 3. Things & Tech to Keep in Mind

### MCP & Tool Design

- **Small, composable tools** are easier for LLMs to use correctly.
- Clearly separate:
  - **Read‑only resources** (MCP resources) from
  - **Effectful operations** (tools that mutate accounts or send notifications).
- Prefer simple, typed parameters over free‑form strings when possible.

### Environment & Secrets

- Never hardcode keys; always use `.env` + `os.getenv`.
- Provide sensible defaults for local dev (e.g., random share prices if `POLYGON_API_KEY` is missing).

### Error Handling

- Make domain methods raise **meaningful exceptions**:
  - e.g., “Insufficient funds”, “Unrecognized symbol”.
- In agents, catch broad exceptions at the top level:
  - Log them and continue to next cycle.

### Determinism vs Randomness

- Market fallback randomness is fine for demos, but:
  - For tests, consider seeding or mocking `get_share_price`.
  - For real use, rely on external APIs only.

### Testing Strategy (Minimum)

- Unit tests for:
  - `Account` methods (buy/sell, report, PnL).
  - `market.get_share_price` with mocked Polygon client.
  - `database.write_*` / `read_*` for a temporary DB.
- Optional integration tests:
  - Launch MCP servers and call tools via MCP client.

---

## 4. Development Checklist

Use this checklist when extending or refactoring the system:

- **Domain & DB**
  - [ ] Account and Transaction models cover all needed fields.
  - [ ] Database schema and helpers are in sync.
  - [ ] Market data functions have a clear fallback strategy.

- **External APIs**
  - [ ] All required env vars are documented and validated at startup.
  - [ ] Failure modes are logged and do not crash the orchestrator.

- **MCP Servers**
  - [ ] Tools and resources are minimal, well‑named, and documented.
  - [ ] Servers delegate to domain code, not vice versa.

- **Agents & Prompts**
  - [ ] Trader and Researcher instructions are aligned with desired behavior.
  - [ ] Tool descriptions explain clearly when/how to use each tool.

- **Orchestrator & UI**
  - [ ] Orchestrator schedules traders and handles errors robustly.
  - [ ] UI is read‑only and reflects DB/log state clearly.

- **Observability & Docs**
  - [ ] Tracing/logging provides enough information to debug behavior.
  - [ ] `README.md`, `docs/architrcture.md`, and `docs/LLD.md` are kept up to date.

This guide should give you a practical roadmap for recreating or extending this MCP‑based autonomous trading system in a clean, incremental way.

