# Evaluations (Evals) — Agentic Trading System

This project currently **does not include a formal eval suite**. This document outlines practical ways to add evals for the agentic trading stack (OpenAI Agents SDK, MCP, tools) and how to run them.

---

## Current state

- **Unit tests**: `tests/test_accounts.py` covers `Account`, `Transaction`, deposit/withdraw/report. No agent or MCP behavior is tested.
- **No LLM evals**: No regression tests on prompts, tool choice, or final outputs.
- **No scenario evals**: No end-to-end “given this account and strategy, the agent should do X” tests.

---

## Why evals matter here

- **Non-determinism**: Model outputs and tool sequences vary; evals help catch regressions (e.g. “agent stopped using research before trading”).
- **Prompt drift**: Changes to `templates.py` or instructions can change behavior; evals lock in desired behavior.
- **MCP and tools**: Tool schemas and server availability change; evals verify the agent still discovers and uses the right tools.
- **Safety and correctness**: Evals can assert “no sell without holdings”, “no buy with insufficient balance”, “rationale present”, etc.

---

## 1. What to evaluate

### 1.1 Domain / tool correctness (deterministic)

- **Account logic** (already partially covered by `test_accounts.py`): extend with `buy_shares` / `sell_shares` edge cases (zero quantity, negative, unknown symbol, insufficient funds, insufficient shares).
- **MCP tool contracts**: for each tool (`get_balance`, `get_holdings`, `buy_shares`, `sell_shares`, etc.), eval that with a given input the output shape and semantics are correct (e.g. via small integration tests against the real MCP server or a mock).
- **Market data**: mock `get_share_price` and assert portfolio value and PnL are computed correctly after a sequence of buys/sells.

### 1.2 Agent behavior (LLM-in-the-loop)

- **Tool use**: given a fixed scenario (account state + strategy + message), assert that the agent calls certain tools (e.g. at least one of `get_balance`/`get_holdings`, and when appropriate `buy_shares` or `sell_shares`) and does not call disallowed tools.
- **Order correctness**: agent never sells more than holdings, never buys with insufficient balance (can be enforced in code too; evals double-check agent doesn’t try).
- **Rationale**: every `buy_shares` / `sell_shares` call includes a non-empty `rationale` that can be checked (e.g. length > 0, or keyword presence).
- **Strategy alignment**: optional; use a second LLM or rules to score whether the agent’s reasoning and trades align with the given strategy text (e.g. “value” vs “momentum”).

### 1.3 Researcher agent

- **Tool use**: with a fixed “research request” message, assert the Researcher uses search/fetch and/or memory tools and returns a coherent summary (e.g. not empty, no “I cannot” when tools are available).
- **No trading**: Researcher must never call `buy_shares` / `sell_shares` (only the Trader should); evals can assert tool-call names.

### 1.4 MCP and integration

- **Server startup**: evals that start the accounts (and optionally market) MCP servers and call one or two tools, then shut down (smoke test).
- **Tool discovery**: after connecting to MCP, assert the expected tool list is present (e.g. `buy_shares`, `sell_shares`, `get_balance`, `get_holdings`).
- **Resource availability**: `accounts://accounts_server/{name}` and `accounts://strategy/{name}` return valid JSON / text for a known test account.

### 1.5 End-to-end scenarios (expensive but valuable)

- **Scenario A – “Do nothing”**: account with 0 balance, strategy “preserve capital”. Expect: no buy/sell calls (or only attempted buys that are rejected by the tool).
- **Scenario B – “Single buy”**: account with cash, strategy “buy one share of AAPL”. Expect: one `buy_shares` for AAPL, quantity ≥ 1.
- **Scenario C – “Rebalance”**: account holding one ticker, strategy “diversify”. Expect: at least one sell or one buy for a different ticker (or both).
- **Scenario D – “Research then trade”**: expect at least one Researcher (or search) use before the first `buy_shares`/`sell_shares` in a trade run.

These can be run less frequently (e.g. nightly or pre-release) and with a cheaper/smaller model to control cost.

---

## 2. How to implement evals

### 2.1 Stack and tooling

- **Pytest**: keep using it; add new test modules e.g. `tests/test_agent_evals.py`, `tests/test_mcp_tools.py`.
- **Mocking**:
  - **Market**: patch `market.get_share_price` (and any Polygon calls) so evals don’t depend on live data.
  - **LLM**: for deterministic “tool contract” tests, avoid the real model; for agent evals, use the real API or a small model (e.g. `gpt-4o-mini`) with fixed seed if supported.
- **MCP**: run real MCP servers in process or subprocess for integration evals; use a test DB and test account (e.g. `eval_test`) so no production data is touched.
- **OpenAI Evals / custom harness**: you can use the [OpenAI Evals](https://github.com/openai/evals) framework or a simple custom runner that:
  - builds the same `Trader` + MCP stack as production,
  - sends a fixed message and captures tool calls and final response,
  - runs assertions (e.g. “tool X was called”, “no sell of more than holdings”).

### 2.2 Suggested layout

```text
tests/
  test_accounts.py          # existing
  test_market.py            # optional: market mocks and portfolio value
  test_mcp_accounts.py      # MCP accounts server smoke + tool contracts
  test_agent_evals.py       # agent behavior (tool use, rationale, scenarios)
  fixtures/                 # optional: JSON account snapshots, expected tool lists
```

### 2.3 Example: agent eval (pseudo-code)

```python
# tests/test_agent_evals.py (conceptual)
import pytest
from unittest.mock import patch

@pytest.mark.asyncio
async def test_trader_uses_account_tools_before_trading():
    # Use test account + mock market; run one cycle
    with patch("src.accounts.get_share_price", return_value=100.0):
        # Build Trader, run with trade_message, capture Runner.run tool_calls
        tool_names = [...]  # from trace or runner callback
    assert "get_balance" in tool_names or "get_holdings" in tool_names
    # Optionally: first buy/sell only after get_holdings or get_balance
```

### 2.4 Example: MCP tool contract

```python
# tests/test_mcp_accounts.py (conceptual)
@pytest.mark.asyncio
async def test_buy_shares_rejects_insufficient_funds():
    # Start accounts_server MCP, get client, call buy_shares with balance < cost
    # Assert result is error or message indicating failure (no balance change)
```

---

## 3. Specific implementation examples (where and how in `src/`)

The following are concrete insertion points and code patterns using the current codebase.

### 3.1 `tests/test_accounts.py` — extend account and order evals

**Existing pattern:** `DummyAccount` overrides `save()` to avoid DB writes; tests patch `src.accounts.get_share_price` and `src.accounts.write_log` (see `test_report_returns_valid_json`).

**Add evals for `buy_shares` and `sell_shares` (src/accounts.py lines 97–145):**

```python
# tests/test_accounts.py — add these

def test_buy_shares_insufficient_funds(monkeypatch):
    monkeypatch.setattr("src.accounts.get_share_price", lambda s: 100.0)
    monkeypatch.setattr("src.accounts.write_log", lambda *a, **k: None)
    acct = DummyAccount(
        name="eval_buy", balance=50.0, strategy="", holdings={},
        transactions=[], portfolio_value_time_series=[],
    )
    with pytest.raises(ValueError, match="Insufficient funds"):
        acct.buy_shares("AAPL", 1, "test")

def test_buy_shares_unrecognized_symbol(monkeypatch):
    monkeypatch.setattr("src.accounts.get_share_price", lambda s: 0.0)
    monkeypatch.setattr("src.accounts.write_log", lambda *a, **k: None)
    acct = DummyAccount(
        name="eval_buy", balance=10_000.0, strategy="", holdings={},
        transactions=[], portfolio_value_time_series=[],
    )
    with pytest.raises(ValueError, match="Unrecognized symbol"):
        acct.buy_shares("INVALID", 1, "test")

def test_sell_shares_insufficient_holdings(monkeypatch):
    monkeypatch.setattr("src.accounts.get_share_price", lambda s: 100.0)
    monkeypatch.setattr("src.accounts.write_log", lambda *a, **k: None)
    acct = DummyAccount(
        name="eval_sell", balance=0, strategy="", holdings={"AAPL": 5},
        transactions=[], portfolio_value_time_series=[],
    )
    with pytest.raises(ValueError, match="Not enough shares"):
        acct.sell_shares("AAPL", 10, "test")

def test_buy_then_sell_balance_and_holdings(monkeypatch):
    monkeypatch.setattr("src.accounts.get_share_price", lambda s: 100.0)
    monkeypatch.setattr("src.accounts.write_log", lambda *a, **k: None)
    acct = DummyAccount(
        name="eval_flow", balance=5_000.0, strategy="", holdings={},
        transactions=[], portfolio_value_time_series=[],
    )
    acct.buy_shares("AAPL", 10, "eval buy")
    assert acct.holdings.get("AAPL") == 10
    acct.sell_shares("AAPL", 3, "eval sell")
    assert acct.holdings.get("AAPL") == 7
    assert len(acct.transactions) == 2
```

**Where:** Same file as existing tests; reuse `DummyAccount` and `monkeypatch` so no real DB or market is used.

---

### 3.2 `tests/test_mcp_accounts.py` (new) — MCP tool and resource evals

**Entry points in src:** `accounts_client.py` exposes `list_accounts_tools()`, `call_accounts_tool(tool_name, tool_args)`, `read_accounts_resource(name)`, `read_strategy_resource(name)`. The MCP server is started via `StdioServerParameters(command="uv", args=["run", "accounts_server.py"], env=None)` and uses `database.DB = "accounts.db"` (database.py line 8).

**Use a dedicated test account and optional test DB:**

- Create a test account (e.g. `eval_test`) in the DB before MCP tests, or set `env` in `StdioServerParameters` to point the server to a test DB (would require making `database.DB` overridable via env, e.g. `DB = os.getenv("ACCOUNTS_DB", "accounts.db")`).
- Run from repo root with `cd src` so `uv run accounts_server.py` finds the module; or run pytest from project root with `PYTHONPATH=src` so `accounts_client` and `database` use the same `src` layout.

**Example: tool discovery (deterministic)**

```python
# tests/test_mcp_accounts.py
import pytest
import os
# Ensure we run from project root and use test DB if set
# export ACCOUNTS_DB=test_accounts.db for isolation

@pytest.mark.asyncio
async def test_accounts_mcp_tools_list():
    from src.accounts_client import list_accounts_tools
    tools = await list_accounts_tools()
    names = [t.name for t in tools]
    assert "get_balance" in names
    assert "get_holdings" in names
    assert "buy_shares" in names
    assert "sell_shares" in names
    assert "change_strategy" in names
```

**Example: tool contract — get_balance returns a number**

```python
@pytest.mark.asyncio
async def test_get_balance_returns_number():
    from src.accounts_client import call_accounts_tool
    # Assumes an account "eval_test" exists with known state (e.g. created in conftest)
    result = await call_accounts_tool("get_balance", {"name": "eval_test"})
    content = result.content
    # MCP tool result shape: typically list of ContentPart with text
    text = content[0].text if hasattr(content[0], "text") else str(content)
    balance = float(text)
    assert balance >= 0
```

**Example: buy_shares rejects insufficient funds**

```python
@pytest.mark.asyncio
async def test_buy_shares_rejects_insufficient_funds():
    from src.accounts_client import call_accounts_tool
    # Use an account with zero balance (seeded in conftest or fixture)
    result = await call_accounts_tool("buy_shares", {
        "name": "eval_test",
        "symbol": "AAPL",
        "quantity": 1000,
        "rationale": "eval test",
    })
    text = result.content[0].text if result.content else str(result)
    assert "insufficient" in text.lower() or "error" in text.lower()
```

**Example: resources return valid JSON / text**

```python
@pytest.mark.asyncio
async def test_read_accounts_resource_returns_json():
    from src.accounts_client import read_accounts_resource
    import json
    raw = await read_accounts_resource("eval_test")
    data = json.loads(raw)
    assert "name" in data
    assert "balance" in data
    assert "holdings" in data

@pytest.mark.asyncio
async def test_read_strategy_resource_returns_string():
    from src.accounts_client import read_strategy_resource
    strategy = await read_strategy_resource("eval_test")
    assert isinstance(strategy, str)
```

**Where:** New file `tests/test_mcp_accounts.py`; optionally `tests/conftest.py` to create a test account and/or set `ACCOUNTS_DB` for the process (note: accounts_server subprocess may need the same env to use the test DB).

---

### 3.3 `tests/test_agent_evals.py` (new) — agent behavior evals

**Entry points in src:** `traders.py`: `Trader.run_agent(trader_mcp_servers, researcher_mcp_servers)` builds the message with `trade_message(self.name, strategy, account)` or `rebalance_message(...)` (lines 154–157) and calls `Runner.run(self.agent, message, max_turns=MAX_TURNS)` (line 160). The agent is built in `create_agent()` (lines 124–135) with `trader_instructions(self.name)` from `templates.trader_instructions`.

**Challenge:** `Runner.run()` is from the external `agents` SDK; we don’t control its return value. Two options:

1. **Trace/log inspection:** The app registers `LogTracer()` in `trading_floor.py`, which writes to the DB via `database.write_log`. Run one trader cycle with a test account and fixed strategy, then assert on the last N log entries (e.g. that at least one log line contains a tool name like `buy_shares` or `get_holdings`). Requires a test DB and seeding a test account.
2. **Wrap or mock Runner.run:** In the test, patch `Runner.run` to record the `message` and `agent` and return a fixed response; then assert on the message (e.g. contains the injected account state). For tool-call assertions, if the SDK exposes a way to capture tool calls (e.g. via a callback or trace), use that in the test.

**Example: deterministic message construction (no LLM)**

```python
# tests/test_agent_evals.py
import pytest
from unittest.mock import AsyncMock, patch

def test_trade_message_contains_account_name():
    from src.templates import trade_message
    account_json = '{"name":"EvalTrader","balance":10000,"holdings":{}}'
    msg = trade_message("EvalTrader", "Buy low.", account_json)
    assert "EvalTrader" in msg
    assert "Your account name is EvalTrader" in msg
    assert "10000" in msg or "account" in msg

def test_rebalance_message_contains_strategy():
    from src.templates import rebalance_message
    account_json = '{"name":"Eval","balance":5000,"holdings":{"AAPL":10}}'
    msg = rebalance_message("Eval", "Diversify.", account_json)
    assert "rebalance" in msg.lower() or "portfolio" in msg.lower()
    assert "Eval" in msg
```

**Example: agent run with mocked MCP and captured tool calls (if SDK allows)**

If the Runner or Agent API lets you pass a callback or retrieve tool calls after `Runner.run`, use it like this (pseudo-code; adapt to actual SDK):

```python
@pytest.mark.asyncio
async def test_trader_run_calls_account_tools(monkeypatch):
    # 1) Mock account and strategy so no real MCP needed
    fixed_account = '{"name":"eval","balance":10000,"holdings":{},"transactions":[]}'
    fixed_strategy = "Preserve capital. Do not take large risks."
    from src.traders import Trader
    from src.templates import trade_message

    async def mock_read_account(_): return fixed_account
    async def mock_read_strategy(_): return fixed_strategy
    monkeypatch.setattr("src.traders.read_accounts_resource", mock_read_account)
    monkeypatch.setattr("src.traders.read_strategy_resource", mock_read_strategy)

    # 2) Optional: mock Runner.run to capture message and fake tool calls
    tool_calls_captured = []
    async def capture_run(agent, message, max_turns=None):
        tool_calls_captured.append(("message", message))
        # Simulate one turn and return
        return None
    monkeypatch.setattr("src.traders.Runner.run", capture_run)

    # 3) Run would need mocked MCP servers (empty list or minimal) so create_agent doesn’t fail
    # Then run_agent; then assert on tool_calls_captured or on logs
```

**Example: assert on logs after a real short run (integration)**

```python
@pytest.mark.asyncio
async def test_trader_run_writes_logs():
    import os
    os.environ["ACCOUNTS_DB"] = "test_accounts.db"  # if supported
    # Seed eval_test account, then create Trader("EvalTest"), run one cycle
    # with mocked researcher MCP to avoid network; then read_log("evaltest", last_n=20)
    # and assert any("get_balance" in str(log) or "get_holdings" in str(log) for log in logs)
```

**Where:** New file `tests/test_agent_evals.py`; prompt/message evals are fully deterministic; agent tool-use evals depend on trace/log or SDK support for capturing tool calls.

---

### 3.4 `src/templates.py` — prompt surface for evals

**Functions to target:** `trader_instructions(name)` (line 36), `trade_message(name, strategy, account)` (49), `rebalance_message(name, strategy, account)` (69), `researcher_instructions()` (12).

**Eval ideas:**

- **Deterministic:** For a fixed `name`, assert `trader_instructions(name)` contains `name` and does not contain placeholder leakage (e.g. `{api_key}`).
- **Deterministic:** For fixed inputs, assert `trade_message` and `rebalance_message` include the given strategy snippet and account name.
- **Regression:** Store a golden snippet of instructions (e.g. “use your account name”) and assert it still appears after template edits.

```python
# tests/test_templates.py (new or inside test_agent_evals.py)
def test_trader_instructions_includes_account_name():
    from src.templates import trader_instructions
    t = trader_instructions("Warren")
    assert "Warren" in t
    assert "account" in t.lower()
```

---

### 3.5 `src/market.py` and portfolio value

**Entry points:** `get_share_price(symbol)` (line 69), used by `Account.calculate_portfolio_value()` and inside `buy_shares`/`sell_shares`.

**Eval:** In `tests/test_accounts.py` (or `tests/test_market.py`), patch `get_share_price` to return known prices and assert `calculate_portfolio_value()` and PnL match expectations after a sequence of buys/sells. Already partially covered by the “buy then sell” style test; you can add an explicit portfolio-value assertion.

```python
def test_portfolio_value_after_trades(monkeypatch):
    monkeypatch.setattr("src.accounts.get_share_price", lambda s: 100.0)
    monkeypatch.setattr("src.accounts.write_log", lambda *a, **k: None)
    acct = DummyAccount(
        name="pv", balance=10_000.0, strategy="", holdings={},
        transactions=[], portfolio_value_time_series=[],
    )
    acct.buy_shares("AAPL", 20, "eval")
    pv = acct.calculate_portfolio_value()
    # balance after buy: 10000 - 20*100*1.002; holdings 20*100
    assert pv > 0
    assert acct.holdings.get("AAPL") == 20
```

---

### 3.6 Summary table (where to add evals)

| Eval target              | File / location                      | How (deterministic vs LLM) |
|--------------------------|--------------------------------------|----------------------------|
| `Account.buy_shares`     | `tests/test_accounts.py`             | DummyAccount + monkeypatch `get_share_price`, `write_log`; assert ValueError or state |
| `Account.sell_shares`    | `tests/test_accounts.py`            | Same pattern; assert insufficient holdings rejected |
| `Account.report` / PnL   | `tests/test_accounts.py`            | Already present; extend with portfolio_value after trades |
| MCP tool list            | `tests/test_mcp_accounts.py`        | `list_accounts_tools()` from `accounts_client`; assert tool names |
| MCP tool contract        | `tests/test_mcp_accounts.py`        | `call_accounts_tool(name, args)`; assert return shape and semantics |
| MCP resources            | `tests/test_mcp_accounts.py`        | `read_accounts_resource`, `read_strategy_resource`; assert JSON/string |
| Message construction     | `tests/test_agent_evals.py` or `test_templates.py` | `trade_message`, `rebalance_message`, `trader_instructions` with fixed inputs |
| Agent tool use / scenario| `tests/test_agent_evals.py`         | Run Trader with mocked MCP + account/strategy; assert on logs or captured tool calls (if SDK supports) |
| Templates                | `tests/test_templates.py` or above   | Assert instructions contain required phrases and no secret placeholders |

---

## 4. Running evals

- **CI**: run deterministic tests (unit + MCP smoke + tool contracts) on every commit; skip or gate expensive agent evals (e.g. only on main or nightly).
- **Local**: `uv run pytest tests/ -v`; for agent evals, set env (e.g. `OPENAI_API_KEY`) and optionally `--run-llm-evals` or a marker.
- **Cost control**: use a small/cheap model and fixed seed for agent evals; limit number of turns and runs per scenario.

---

## 5. Summary

| Eval type              | Deterministic? | Where              | Purpose                          |
|------------------------|----------------|--------------------|----------------------------------|
| Account/tool logic     | Yes            | `test_accounts.py` | Correctness of buy/sell/report   |
| MCP tool contracts     | Yes            | `test_mcp_*.py`    | Tools behave as specified        |
| MCP discovery/resources| Yes            | `test_mcp_*.py`    | Servers and resources available  |
| Agent tool use         | No (LLM)       | `test_agent_evals` | Agent uses expected tools        |
| Agent scenarios        | No (LLM)       | `test_agent_evals` | E2E behavior for key scenarios   |
| Researcher behavior    | No (LLM)       | `test_agent_evals` | Research tool used, no trading    |

Adding evals incrementally: start with MCP smoke + tool contracts and account edge cases, then add a few agent-scenario evals for high-value flows (e.g. “no trade without research”, “rationale present”, “no over-sell”). This will make prompt and MCP changes safer and move the project toward production-grade behavior validation.
