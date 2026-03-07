# MCP Server Concepts — Build and Use

This document explains the basics of **building** and **using** MCP (Model Context Protocol) servers, with two concrete examples. Tool usage is driven by **instructions** (agent behavior) and **request prompts** (user query).

---

## 1. Basics: What Is an MCP Server?

An MCP server exposes **tools** (and optionally **resources**, **prompts**) to an AI agent. The agent runs in a client (e.g. OpenAI Agents SDK); the client talks to the server over **stdio** or **SSE**. The agent decides **when** and **which** tools to call based on:

1. **Instructions** — system/agent instructions that describe the agent’s role and how it should use tools.
2. **Request** — the user’s message (e.g. “What’s the latest news on Tesla?”).

So: **instructions + request → agent chooses tools → server runs them → agent uses results in the reply.**

---

## 2. Types of MCP Servers (from Lab 3)

| Type | Description | Example |
|------|-------------|---------|
| **Local only** | Runs locally, no external API | Memory (mcp-memory-libsql) |
| **Local → web** | Runs locally, calls a web API | Brave Search, Polygon |
| **Remote** | Hosted elsewhere; less common | Anthropic/Cloudflare hosted MCPs |

We focus on **local** and **local → web**; both are started as child processes (e.g. `npx`, `uv run`).

---

## 3. How to *Use* an MCP Server (Client Side)

Same pattern for any MCP server:

1. **Start the server** with the right `command`, `args`, and `env` (e.g. API keys).
2. **List tools** (e.g. `server.list_tools()`) to see names and schemas.
3. **Create an agent** with `instructions` and attach the server via `mcp_servers=[...]`.
4. **Run the agent** with a `request`; the model uses instructions + request to decide which tools to call.

Instructions and request are the main knobs for “how” the tools get used.

---

## 4. Example 1: Brave Search MCP (Use an Existing Server)

We **do not build** this server; we **run** the official Brave Search MCP and **use** its tools via instructions and request.

### 4.1 How we run it

- **Command:** run the npm package with `npx`; pass the API key in `env`.

```python
import os
from agents.mcp import MCPServerStdio

env = {"BRAVE_API_KEY": os.getenv("BRAVE_API_KEY")}
params = {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-brave-search"],
    "env": env,
}

async with MCPServerStdio(params=params, client_session_timeout_seconds=30) as server:
    mcp_tools = await server.list_tools()
```

- Get a free key at [brave.com/search/api](https://brave.com/search/api/) and set `BRAVE_API_KEY` in `.env`.

### 4.2 What tools are available

| Tool | Purpose |
|------|--------|
| `brave_web_search` | Web search: general queries, news, articles. Params: `query`, optional `count` (1–20), `offset` for pagination. |
| `brave_local_search` | Local businesses/places (e.g. “pizza near Central Park”). Params: `query`, optional `count`. |

So: **two tools** — one for the web, one for local search.

### 4.3 How we use them: instructions + request

The agent has **no built-in rule** to “always use Brave.” We tell it what it can do and what we want via **instructions** and **request**.

**Instructions** (what the agent is allowed to do and how to behave):

```python
instructions = "You are able to search the web for information and briefly summarize the takeaways."
```

**Request** (what we ask; this pushes the model toward calling the tool):

```python
request = (
    "Please research the latest news on Tesla stock price and briefly summarize its outlook. "
    f"For context, the current date is {datetime.now().strftime('%Y-%m-%d')}."
)
```

**Run:**

```python
async with MCPServerStdio(params=params, client_session_timeout_seconds=30) as mcp_server:
    agent = Agent(
        name="agent",
        instructions=instructions,
        model="gpt-4o-mini",
        mcp_servers=[mcp_server],
    )
    result = await Runner.run(agent, request)
```

Flow in words:

1. **Instructions** say: “you can search the web and summarize.”
2. **Request** asks for “latest news on Tesla… summarize outlook.”
3. The model infers it needs current web content → calls `brave_web_search` with a suitable query.
4. It uses the search results to produce a short summary.

So: **instructions + request drive which tool is used and how.**

---

## 5. Example 2: Building Our Own MCP (Market Server)

Here we **build** a small MCP server that exposes one tool: current share price. Then we use it the same way: instructions + request.

### 5.1 How we build it

- Use **FastMCP** (Python). One server process, one or more tools.
- **Define tools** as async functions; docstrings and type hints become the tool schema the client sees.

**File: `market_server.py`**

```python
from mcp.server.fastmcp import FastMCP
from market import get_share_price

mcp = FastMCP("market_server")

@mcp.tool()
async def lookup_share_price(symbol: str) -> float:
    """This tool provides the current price of the given stock symbol.

    Args:
        symbol: the symbol of the stock
    """
    return get_share_price(symbol)

if __name__ == "__main__":
    mcp.run(transport='stdio')
```

Steps in short:

1. Create `FastMCP("market_server")`.
2. Register a tool with `@mcp.tool()`; implement the function (here: call `get_share_price`).
3. Run with `mcp.run(transport='stdio')` so the client talks over stdio.

The client will see a tool named `lookup_share_price` with one argument `symbol` (string) and a numeric result.

### 5.2 What tools are available

| Tool | Purpose |
|------|--------|
| `lookup_share_price` | Current price for a given stock symbol. Input: `symbol` (e.g. `"AAPL"`). Output: price (float). |

So: **one tool** for share price lookup.

### 5.3 How we use them: instructions + request

Same idea as Brave: we don’t hard-code “call lookup_share_price”; we describe the agent’s role and ask a question that implies using the tool.

**Instructions:**

```python
instructions = "You answer questions about the stock market."
```

**Request:**

```python
request = "What's the share price of Apple?"
```

**Run:**

```python
params = {"command": "uv", "args": ["run", "market_server.py"]}

async with MCPServerStdio(params=params, client_session_timeout_seconds=60) as mcp_server:
    agent = Agent(
        name="agent",
        instructions=instructions,
        model="gpt-4.1-mini",
        mcp_servers=[mcp_server],
    )
    result = await Runner.run(agent, request)
```

Flow:

1. **Instructions** say: “you answer questions about the stock market.”
2. **Request** asks: “What’s the share price of Apple?”
3. The model infers it needs a price → calls `lookup_share_price(symbol="AAPL")`.
4. It uses the returned price in its answer.

So again: **instructions + request drive tool use.**

---

## 6. Summary: Instructions and Request

- **Instructions** = agent’s role and how it may use tools (e.g. “search the web and summarize”, “answer stock market questions”).
- **Request** = user message; the more specific it is (e.g. “latest Tesla news”, “Apple share price”), the more likely the right tool is used.
- The **model** decides which tools to call and with what arguments; we don’t call tools by name in code. We only configure the server, pass instructions + request, and run the agent.

### Quick reference

| Step | Brave Search | Our market_server |
|------|----------------|-------------------|
| Run server | `npx -y @modelcontextprotocol/server-brave-search` + `BRAVE_API_KEY` | `uv run market_server.py` |
| Tools | `brave_web_search`, `brave_local_search` | `lookup_share_price` |
| Use | Instructions: “search web and summarize”; Request: “latest Tesla news” | Instructions: “answer stock market questions”; Request: “Apple share price?” |

For more on how this project wires MCP into traders and researchers, see **`docs/LLD.md`** and **`AGENTS.md`**.
