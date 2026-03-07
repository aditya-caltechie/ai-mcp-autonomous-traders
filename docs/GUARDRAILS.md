# Guardrails — Trading Safety and Production Controls

This project **does not currently implement explicit guardrails** beyond basic checks in `accounts.py` (insufficient funds, unrecognized symbol, insufficient shares to sell). This document outlines guardrail ideas to make the system safer and production-ready.

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

## 7. Other production-readiness considerations

Beyond **guardrails** (this doc) and **evals** (see `docs/EVALS.md`), the following help make the system production-level:

### 7.1 Observability and alerting

- **Tracing**: already in place (OpenAI traces + `LogTracer` to SQLite); see `docs/6_OBSERVABILITY.md`.
- **Alerting**: no alerts today. Add alerts for: trader run failures, MCP server crashes, drawdown threshold, kill switch activated, or daily limit hit. Can be wired to Pushover (existing push server) or PagerDuty/Slack.
- **Metrics**: optional counters/gauges (trades per run, tool call latency, LLM latency) for dashboards (e.g. Grafana) and SLOs.

### 7.2 Secrets and configuration

- **Secrets**: keep API keys and credentials in env or a secret manager; never in code or prompts. Rotate keys periodically.
- **Config per environment**: separate config for dev/staging/prod (e.g. different DB paths, allowlists, or “read-only” in non-prod).

### 7.3 Audit and compliance

- **Audit log**: persist an immutable log of all trading actions (who, when, symbol, quantity, rationale, outcome). Current `write_log` and transactions are a start; consider a dedicated audit table that is append-only.
- **Replay**: ability to replay a past run (same account snapshot + message) for debugging or compliance review; requires storing inputs and optionally trace IDs.

### 7.4 Reliability and recovery

- **Idempotency**: for real broker integrations, use idempotency keys on orders to avoid double execution on retries.
- **Graceful degradation**: if one MCP server (e.g. market) is down, the agent could still report and rebalance from cached data or skip trading and only log; document expected behavior.
- **Disaster recovery**: backup `accounts.db` and config; document restore and reset procedures (see `reset.py`).

### 7.5 Versioning and change management

- **Prompt and strategy versioning**: track which template/strategy version was used for each run (e.g. store version or hash in logs) so you can correlate behavior changes with prompt edits.
- **Model and SDK versions**: pin or record OpenAI SDK and model names so evals and behavior are reproducible.

### 7.6 Documentation and runbooks

- **Runbooks**: how to pause trading, clear a stuck run, restore from backup, and run evals (see `docs/EVALS.md`).
- **README and AGENTS.md**: keep setup, env vars, and main commands up to date so new contributors and operators can run the system safely.

---

Together, **evals** (behavior and regression), **guardrails** (safety and limits), and the items above (observability, secrets, audit, reliability, versioning, runbooks) form a practical path to production-level agentic trading.
