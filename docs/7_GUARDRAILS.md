# Guardrails — Trading Safety and Production Controls

This project **does not currently implement explicit guardrails** beyond basic checks in `accounts.py` (insufficient funds, unrecognized symbol, insufficient shares to sell). This document outlines guardrail ideas to make the system safer and production-ready.

---

## What are guardrails?

**Guardrails** are rules and checks that restrict what the system is allowed to do. They sit between the agent (or any caller) and the actual execution of actions—such as placing a trade or changing strategy. Guardrails can:

- **Validate inputs** before they are used (e.g. symbol format, quantity range, non-empty rationale).
- **Enforce limits** (e.g. max order size, max position size, daily trade count).
- **Block disallowed actions** (e.g. tickers not on an allowlist, or trading when a kill switch is on).
- **Apply risk controls** (e.g. pause trading after a drawdown, or when market is closed).

They are implemented in code (and sometimes in prompts as soft guidance) so that even if the LLM suggests a bad or extreme action, the system refuses to execute it.

---

## Why guardrails are important and needed

- **LLMs are non-deterministic and can err.** A model might output `quantity: 10000` instead of `10`, or choose an invalid symbol. Without guardrails, a single bad tool call can move large size or corrupt state.
- **Autonomous agents act without human approval.** This project runs traders on a schedule; there is no human in the loop on each order. Guardrails are the main way to cap damage from bugs, prompt drift, or model mistakes.
- **Trading has real risk.** Even with simulated accounts, the logic is the same as for real money. Order size limits, position limits, and kill switches prevent runaway behavior (e.g. thousands of orders in one run).
- **Compliance and audit.** Allowlists, blocklists, and “trading paused” flags support policy (e.g. “only these symbols”) and incident response (e.g. disable trading immediately).
- **Production readiness.** A system that can say “no” to invalid or dangerous requests is safer to deploy and operate than one that blindly executes whatever the agent asks.

In short: guardrails turn an autonomous trading system from “do whatever the agent says” into “do only what is allowed and within limits,” which is essential for safety and control.

---

## Current behavior (no guardrails)

- **Accounts** (`accounts.py`): `buy_shares` rejects if cost > balance or symbol price is 0; `sell_shares` rejects if quantity > holdings. No position limits, ticker allowlist, or order size caps.
- **MCP tools** (`accounts_server.py`): tools pass through to `Account` with no extra validation (e.g. symbol allowlist, max quantity, rate limits).
- **Agent**: no prompt-level or post-response checks that block dangerous tool arguments (e.g. buying 1M shares, trading unknown symbols).

---

## 1. Order and position guardrails

### 1.1 Order size limits

- **Max quantity per order**: cap `quantity` in `buy_shares` / `sell_shares` (e.g. 1_000 or 10_000 shares per call). Prevents one bad LLM call from moving huge size.
- **Max notional per order**: reject if `quantity * price` exceeds a threshold (e.g. 20% of portfolio or a fixed dollar cap). Protects against typos or model errors (e.g. “10000” instead of “10”).
- **Where**: enforce in `Account.buy_shares` / `sell_shares` and/or in the MCP tool wrapper in `accounts_server.py` so all callers (agent or API) are covered.

### 1.2 Position and concentration limits

- **Max position size**: no single holding exceeds X% of portfolio value (e.g. 25%). Block buys that would push a position over the limit.
- **Max number of positions**: cap number of distinct symbols (e.g. 20) to avoid over-diversification and excessive complexity.
- **Min position size**: optional; avoid dust positions (e.g. block buys below $100 notional) to keep the portfolio manageable.

### 1.3 Ticker allowlist / blocklist

- **Allowlist**: only symbols in a configured set (e.g. S&P 500 or a custom list) are tradable. Reject `buy_shares(symbol="UNKNOWN")` with a clear error.
- **Blocklist**: explicitly disallow certain symbols (e.g. leveraged ETFs, meme names) even if they appear in the allowlist.
- **Where**: in `accounts.py` or in a small `guardrails.py` used by both `Account` and `accounts_server`. Allowlist can be loaded from DB or env/config.

---

## 2. Risk and circuit breakers

### 2.1 Daily / session limits

- **Max trades per day per account**: cap number of `buy_shares` + `sell_shares` per calendar day; return a clear “daily limit reached” so the agent can report instead of retrying.
- **Max notional traded per day**: sum of |quantity * price| for the day; block new orders when exceeded.
- **Cooldown**: optional minimum time between two orders (e.g. 1 minute) to avoid runaway loops.

### 2.2 Drawdown and kill switch

- **Drawdown limit**: if portfolio value falls more than X% from peak (e.g. 15%), disable trading (e.g. `buy_shares`/`sell_shares` return “trading paused due to drawdown”) until manual override or next day.
- **Kill switch**: env flag or DB flag (e.g. `TRADING_DISABLED=1` or `accounts.trading_paused`) that disables all buys/sells system-wide. Useful for incidents or maintenance.

### 2.3 Market hours and data sanity

- **Market hours**: already partially handled by `trading_floor` (e.g. `is_market_open()`). Ensure no live orders are sent when market is closed unless explicitly intended (e.g. paper trading).
- **Stale data**: if using cached or delayed prices, reject orders when last price is older than a threshold (e.g. 1 hour) to avoid trading on stale data.

---

## 3. Input and output guardrails

### 3.1 Tool input validation (MCP / agent)

- **Symbol**: normalize and validate (uppercase, length, allowlist) before calling `Account.buy_shares`/`sell_shares`.
- **Quantity**: integer, positive, and ≤ max order size.
- **Rationale**: require non-empty string; optionally cap length and block obviously invalid content (e.g. only whitespace).
- **Name**: ensure `name` in tool calls matches the account that the agent is supposed to act for (e.g. reject if Trader “Warren” calls `buy_shares(name="George", ...)`). Can be enforced in the MCP layer by deriving `name` from context instead of trusting the agent.

### 3.2 Prompt and instruction guardrails

- **System instructions**: add explicit “never exceed one order per symbol per run” or “never buy more than X% of portfolio in a single order” so the model self-constrains when possible.
- **Structured output**: if the agent ever outputs a “trade plan” before execution, validate that plan against guardrails before turning it into tool calls.

### 3.3 Response / tool output guardrails

- **No PII in logs**: ensure rationale and logs don’t inadvertently capture PII; optional redaction or length limits in `write_log`.
- **Sensitive operations**: log all `change_strategy` and optionally all buy/sell with enough context for audit (already partially there via `write_log`).

---

## 4. Operational and security guardrails

### 4.1 Secrets and config

- **No secrets in prompts**: ensure API keys and credentials are never injected into `trader_instructions` or `trade_message`; use env and server-side config only.
- **Read-only by default**: consider a “read-only” mode where market and account tools are available but `buy_shares`/`sell_shares`/`change_strategy` are disabled or no-op (useful for staging or demos).

### 4.2 Idempotency and double execution

- **Idempotency keys**: for real broker integrations, each order could carry an idempotency key so duplicate agent runs don’t double-submit. Less critical with current SQLite-backed accounts but important for production brokers.
- **Confirm before large order**: optional human-in-the-loop or automated “confirm” step when notional exceeds a threshold (e.g. 10% of portfolio).

### 4.3 Rate limits

- **LLM and MCP**: respect API rate limits (OpenAI, Polygon, Brave, etc.); back off and retry with jitter. Optional per-trader rate limit so one agent can’t starve others.
- **Tool calls per run**: `MAX_TURNS` already limits conversation length; optionally cap total `buy_shares`+`sell_shares` per run (e.g. 5) to avoid runaway trading in one cycle.

---

## 5. Implementation approach

### 5.1 Where to enforce

| Guardrail type           | Recommended place                          |
|--------------------------|--------------------------------------------|
| Order size, notional     | `accounts.py` (buy/sell) or shared helper  |
| Position/concentration   | `accounts.py` (before updating holdings)   |
| Ticker allow/blocklist   | `accounts.py` or `guardrails.py`           |
| Daily/session limits     | `accounts.py` or DB + helper                |
| Drawdown / kill switch   | `accounts.py` or config check at entry     |
| Symbol/quantity/rationale| `accounts_server.py` or shared validation   |
| Name / identity          | `accounts_server.py` (infer from context)  |
| Prompt rules             | `templates.py`                             |

### 5.2 Suggested module

- Add a small **`src/guardrails.py`** (or `src/safety.py`) that:
  - Loads config (env or DB): allowlist, max order size, max notional, daily limits, trading paused.
  - Exposes `check_order(symbol, quantity, price, account, side="buy"|"sell")` → `True` or raises `GuardrailError` with message.
  - Optionally `check_trading_allowed(account)` for kill switch / drawdown.
- Call these from `Account.buy_shares`/`sell_shares` and/or from `accounts_server` before calling `Account`.

### 5.3 Configuration

- Use env vars (e.g. `MAX_ORDER_QUANTITY`, `MAX_POSITION_PCT`, `ALLOWED_TICKERS_FILE`, `TRADING_PAUSED`) or a config table in the DB so you can change limits without code changes.
- Document all guardrail env vars in `README.md` and `developers_guide.md`.

---

## 6. Summary

| Category              | Examples                                                    |
|-----------------------|-------------------------------------------------------------|
| Order size            | Max quantity per order, max notional per order             |
| Position              | Max position %, max symbols, min notional                   |
| Tickers               | Allowlist, blocklist                                       |
| Risk                  | Daily trade/notional limits, drawdown pause, kill switch   |
| Input validation      | Symbol, quantity, rationale, account name                  |
| Operational           | Market hours, stale data, rate limits, idempotency         |
| Security              | No secrets in prompts, read-only mode, audit logging       |

Implementing even a subset (e.g. order size caps, allowlist, kill switch, daily limit) will significantly improve safety and make the system more production-ready. Start with the ones that prevent the highest-impact failures (e.g. oversized single order, wrong symbol, or runaway trading).

---

## 7. Specific implementation examples (where and how in `src/`)

The following are concrete insertion points and code patterns using the current codebase.

### 7.1 `src/accounts.py` — order and risk guardrails

**Location:** `buy_shares` (lines 97–119) and `sell_shares` (lines 121–145).

**Current checks:** `total_cost > self.balance`, `price == 0`, `holdings.get(symbol, 0) < quantity`.

**Add at the start of `buy_shares` (after getting `price`, before balance check):**

```python
# Example: max quantity per order (e.g. from os.getenv("MAX_ORDER_QUANTITY", "1000"))
MAX_ORDER_QUANTITY = int(os.getenv("MAX_ORDER_QUANTITY", "5000"))
if quantity <= 0 or quantity > MAX_ORDER_QUANTITY:
    raise ValueError(f"Quantity must be between 1 and {MAX_ORDER_QUANTITY}.")

# Example: max notional per order (e.g. 20% of portfolio or fixed cap)
portfolio_value = self.calculate_portfolio_value()
max_notional = max(portfolio_value * 0.20, 10_000)  # or from env
if buy_price * quantity > max_notional:
    raise ValueError(f"Order notional exceeds limit (max {max_notional:.0f}).")
```

**Add before updating holdings in `buy_shares` (position concentration):**

```python
# Example: no single position > 25% of portfolio
new_position_value = (self.holdings.get(symbol, 0) + quantity) * buy_price
if new_position_value > portfolio_value * 0.25:
    raise ValueError(f"Buy would exceed 25% position limit for {symbol}.")
```

**Add in both `buy_shares` and `sell_shares` (rationale and daily / kill switch):**

```python
# Rationale required and bounded
if not rationale or not rationale.strip():
    raise ValueError("Rationale is required for every trade.")
if len(rationale) > 500:  # optional cap for logs
    rationale = rationale[:500] + "..."

# Kill switch (env TRADING_PAUSED=1)
if os.getenv("TRADING_PAUSED", "").strip().lower() in ("1", "true", "yes"):
    raise ValueError("Trading is temporarily paused by operator.")

# Daily trade count (e.g. max 10 orders per day per account)
from datetime import datetime
today = datetime.now().strftime("%Y-%m-%d")
today_trades = sum(1 for t in self.transactions if t.timestamp.startswith(today))
if today_trades >= int(os.getenv("MAX_TRADES_PER_DAY", "20")):
    raise ValueError("Daily trade limit reached. Try again tomorrow.")
```

**`change_strategy` (line 188):** add length limit or require non-empty; optionally disable when `TRADING_PAUSED`:

```python
def change_strategy(self, strategy: str) -> str:
    if not strategy or not strategy.strip():
        raise ValueError("Strategy cannot be empty.")
    if os.getenv("TRADING_PAUSED", "").strip().lower() in ("1", "true", "yes"):
        raise ValueError("Trading is paused; strategy changes disabled.")
    # ... rest unchanged
```

---

### 7.2 `src/accounts_server.py` — input validation before calling `Account`

**Location:** MCP tool handlers `buy_shares`, `sell_shares` (lines 24–46). The agent supplies `name`, `symbol`, `quantity`, `rationale`; the server does not know which trader is calling (all traders share the same MCP process), so identity guardrails would require passing caller context (see below).

**Add at the top of each tool (e.g. `buy_shares`):**

```python
# Normalize symbol: uppercase, strip
symbol = str(symbol).strip().upper()
if len(symbol) > 10 or not symbol.isalpha():
    return "Error: Invalid symbol format."

# Quantity: positive integer
if not isinstance(quantity, int) or quantity <= 0:
    return "Error: Quantity must be a positive integer."

# Optional: allowlist (e.g. from env ALLOWED_TICKERS=AAPL,MSFT,GOOGL or a file)
allowed = os.getenv("ALLOWED_TICKERS", "").strip().split(",")
if allowed and [a for a in allowed if a]:
    if symbol not in [a.strip().upper() for a in allowed if a]:
        return f"Error: {symbol} is not in the allowed ticker list."
# Blocklist
blocked = os.getenv("BLOCKED_TICKERS", "").strip().upper().split(",")
if symbol in [b.strip() for b in blocked if b]:
    return f"Error: {symbol} is not permitted for trading."

# Rationale
if not rationale or not str(rationale).strip():
    return "Error: A non-empty rationale is required."
```

Then call `Account.get(name).buy_shares(symbol, quantity, rationale)` as today. Same pattern can be applied in `sell_shares` and optionally in `change_strategy`.

**Account identity (name):** The prompts in `templates.py` say “Your account name is {name}” and the message is built with `trade_message(self.name, ...)` in `traders.py` (line 154). The agent is *instructed* to use its own name, but the MCP server does not verify it. To enforce “only this trader can act on this account,” you would need one of:

- Run one accounts MCP server *per trader* with an env var set to that trader’s name, and in the tool ignore the `name` argument and use the env var; or
- Pass the caller identity in the MCP session/context (if the SDK supports it) and reject tool calls where `name != caller_identity`.

The current architecture uses a single shared `accounts_server` process, so identity enforcement is not implemented; the doc can note this as a future improvement.

---

### 7.3 `src/guardrails.py` (new module) — shared checks

Centralize config and reusable checks so both `accounts.py` and `accounts_server.py` can call them.

**Example shape:**

```python
# src/guardrails.py
import os
from typing import Optional

def get_max_order_quantity() -> int:
    return int(os.getenv("MAX_ORDER_QUANTITY", "5000"))

def get_allowed_tickers() -> list[str]:
    raw = os.getenv("ALLOWED_TICKERS", "").strip()
    if not raw:
        return []  # empty = no allowlist
    return [s.strip().upper() for s in raw.split(",") if s.strip()]

def get_blocked_tickers() -> list[str]:
    raw = os.getenv("BLOCKED_TICKERS", "").strip().upper()
    return [s.strip() for s in raw.split(",") if s.strip()]

def is_trading_paused() -> bool:
    return os.getenv("TRADING_PAUSED", "").strip().lower() in ("1", "true", "yes")

def check_symbol(symbol: str) -> Optional[str]:
    """Returns None if OK, or an error message."""
    s = str(symbol).strip().upper()
    if len(s) > 10 or not s.isalpha():
        return "Invalid symbol format."
    allowed = get_allowed_tickers()
    if allowed and s not in allowed:
        return f"{s} is not in the allowed ticker list."
    if s in get_blocked_tickers():
        return f"{s} is not permitted for trading."
    return None

def check_quantity(quantity: int, side: str = "buy") -> Optional[str]:
    if not isinstance(quantity, int) or quantity <= 0:
        return "Quantity must be a positive integer."
    if quantity > get_max_order_quantity():
        return f"Quantity exceeds max order size ({get_max_order_quantity()})."
    return None
```

Then in `accounts.py` and `accounts_server.py`: `from guardrails import check_symbol, check_quantity, is_trading_paused, ...` and call them before proceeding.

---

### 7.4 `src/templates.py` — prompt-level guardrails

**Location:** `trader_instructions(name)` (lines 36–47) and `trade_message` / `rebalance_message` (49–85).

Add explicit rules the model should follow (soft guardrails):

```python
def trader_instructions(name: str):
    return f"""
You are {name}, a trader on the stock market. Your account is under your name, {name}.
...
You must only use your own account name "{name}" when calling buy or sell tools; never use another trader's name.
Never submit a single order for more than 5% of your portfolio value in notional terms.
Always provide a short, genuine rationale for every buy or sell (at least one sentence).
"""
```

And at the end of `trade_message` / `rebalance_message`:

```text
Remember: use account name "{name}" only; one order per symbol per run is recommended; keep rationale non-empty.
```

This does not replace server-side checks but reduces obviously wrong tool calls.

---

### 7.5 `src/trading_floor.py` — market hours and global pause

**Location:** `run_every_n_minutes()` (lines 40–48). Market hours are already gated with `is_market_open()` and `RUN_EVEN_WHEN_MARKET_IS_CLOSED`.

**Optional:** skip running traders when a global kill switch is set, so no agent runs at all:

```python
# At the start of the loop, before is_market_open()
if os.getenv("TRADING_PAUSED", "").strip().lower() in ("1", "true", "yes"):
    print("Trading is paused (TRADING_PAUSED). Skipping run.")
    await asyncio.sleep(RUN_EVERY_N_MINUTES * 60)
    continue
```

---

### 7.6 `src/market.py` and `src/database.py` — stale data (optional)

**Current behavior:** `get_share_price(symbol)` returns a float; for EOD it uses `get_market_for_prior_date(today)` with cached data in `database.read_market(date)`. There is no “last updated” timestamp exposed.

**Optional guard:** In `accounts.py` inside `buy_shares`/`sell_shares`, if you later add a way to get “last price time” (e.g. from market_server or a small extension to `get_share_price`), reject orders when the price is older than e.g. 1 hour for paid/realtime plans. This would require a small API change in `market.py` (e.g. return a tuple or a small dataclass with price and timestamp).

---

### 7.7 Summary table (where to add what)

| Guardrail           | File               | Where / how                                                                 |
|---------------------|--------------------|-----------------------------------------------------------------------------|
| Max order quantity  | `accounts.py`      | Start of `buy_shares` / `sell_shares`; raise if `quantity > MAX_ORDER_QUANTITY` |
| Max notional/order  | `accounts.py`      | In `buy_shares` after `get_share_price`; compare `quantity * price` to limit    |
| Position % cap      | `accounts.py`      | In `buy_shares` before updating `holdings`; compare new position value to portfolio % |
| Rationale required  | `accounts.py`      | Start of `buy_shares`/`sell_shares`; reject if empty or only whitespace           |
| Kill switch         | `accounts.py`      | Start of `buy_shares`/`sell_shares`/`change_strategy`; check `TRADING_PAUSED`     |
| Daily trade limit   | `accounts.py`      | In `buy_shares`/`sell_shares`; count `self.transactions` for today, compare to env |
| Ticker allow/block  | `accounts_server.py` or `guardrails.py` | Before `Account.get(...).buy_shares`; validate `symbol`                      |
| Symbol/quantity norm| `accounts_server.py` | Start of `buy_shares`/`sell_shares` tools; normalize and validate, return error string |
| Prompt rules        | `templates.py`     | In `trader_instructions` and `trade_message`/`rebalance_message`; add short rules  |
| Global pause        | `trading_floor.py`  | Start of scheduler loop; skip run if `TRADING_PAUSED`                             |
| Shared helpers      | `guardrails.py`    | New module; env-based config and `check_symbol`/`check_quantity`/`is_trading_paused` |

---

## 8. Other production-readiness considerations

Beyond **guardrails** (this doc) and **evals** (see `docs/EVALS.md`), the following help make the system production-level:

### 8.1 Observability and alerting

- **Tracing**: already in place (OpenAI traces + `LogTracer` to SQLite); see `docs/6_OBSERVABILITY.md`.
- **Alerting**: no alerts today. Add alerts for: trader run failures, MCP server crashes, drawdown threshold, kill switch activated, or daily limit hit. Can be wired to Pushover (existing push server) or PagerDuty/Slack.
- **Metrics**: optional counters/gauges (trades per run, tool call latency, LLM latency) for dashboards (e.g. Grafana) and SLOs.

### 8.2 Secrets and configuration

- **Secrets**: keep API keys and credentials in env or a secret manager; never in code or prompts. Rotate keys periodically.
- **Config per environment**: separate config for dev/staging/prod (e.g. different DB paths, allowlists, or “read-only” in non-prod).

### 8.3 Audit and compliance

- **Audit log**: persist an immutable log of all trading actions (who, when, symbol, quantity, rationale, outcome). Current `write_log` and transactions are a start; consider a dedicated audit table that is append-only.
- **Replay**: ability to replay a past run (same account snapshot + message) for debugging or compliance review; requires storing inputs and optionally trace IDs.

### 8.4 Reliability and recovery

- **Idempotency**: for real broker integrations, use idempotency keys on orders to avoid double execution on retries.
- **Graceful degradation**: if one MCP server (e.g. market) is down, the agent could still report and rebalance from cached data or skip trading and only log; document expected behavior.
- **Disaster recovery**: backup `accounts.db` and config; document restore and reset procedures (see `reset.py`).

### 8.5 Versioning and change management

- **Prompt and strategy versioning**: track which template/strategy version was used for each run (e.g. store version or hash in logs) so you can correlate behavior changes with prompt edits.
- **Model and SDK versions**: pin or record OpenAI SDK and model names so evals and behavior are reproducible.

### 8.6 Documentation and runbooks

- **Runbooks**: how to pause trading, clear a stuck run, restore from backup, and run evals (see `docs/EVALS.md`).
- **README and AGENTS.md**: keep setup, env vars, and main commands up to date so new contributors and operators can run the system safely.

---

Together, **evals** (behavior and regression), **guardrails** (safety and limits), and the items above (observability, secrets, audit, reliability, versioning, runbooks) form a practical path to production-level agentic trading.
