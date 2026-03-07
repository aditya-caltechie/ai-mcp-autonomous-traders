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

## 3. Running evals

- **CI**: run deterministic tests (unit + MCP smoke + tool contracts) on every commit; skip or gate expensive agent evals (e.g. only on main or nightly).
- **Local**: `uv run pytest tests/ -v`; for agent evals, set env (e.g. `OPENAI_API_KEY`) and optionally `--run-llm-evals` or a marker.
- **Cost control**: use a small/cheap model and fixed seed for agent evals; limit number of turns and runs per scenario.

---

## 4. Summary

| Eval type              | Deterministic? | Where              | Purpose                          |
|------------------------|----------------|--------------------|----------------------------------|
| Account/tool logic     | Yes            | `test_accounts.py` | Correctness of buy/sell/report   |
| MCP tool contracts     | Yes            | `test_mcp_*.py`    | Tools behave as specified        |
| MCP discovery/resources| Yes            | `test_mcp_*.py`    | Servers and resources available  |
| Agent tool use         | No (LLM)       | `test_agent_evals` | Agent uses expected tools        |
| Agent scenarios        | No (LLM)       | `test_agent_evals` | E2E behavior for key scenarios   |
| Researcher behavior    | No (LLM)       | `test_agent_evals` | Research tool used, no trading    |

Adding evals incrementally: start with MCP smoke + tool contracts and account edge cases, then add a few agent-scenario evals for high-value flows (e.g. “no trade without research”, “rationale present”, “no over-sell”). This will make prompt and MCP changes safer and move the project toward production-grade behavior validation.
