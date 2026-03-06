from accounts_client import read_accounts_resource, read_strategy_resource
from tracers import make_trace_id
from agents import Agent, Tool, Runner, OpenAIChatCompletionsModel, trace
from openai import AsyncOpenAI
from dotenv import load_dotenv
import asyncio
import os
import json
from agents.mcp import MCPServerStdio
from templates import (
    researcher_instructions,
    trader_instructions,
    trade_message,
    rebalance_message,
    research_tool,
)
# MCP configuration:
# - trader_mcp_server_params: internal MCP servers for accounts + push +
#   market (Polygon MCP if available, otherwise local market_server.py).
# - researcher_mcp_server_params: external-ish MCP servers used for research
#   (mcp-server-fetch, Brave search, and per-trader memory-libsql DB).
from mcp_params import trader_mcp_server_params, researcher_mcp_server_params

load_dotenv(override=True)

deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
google_api_key = os.getenv("GOOGLE_API_KEY")
grok_api_key = os.getenv("GROK_API_KEY")
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
GROK_BASE_URL = "https://api.x.ai/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

MAX_TURNS = 30

openrouter_client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=openrouter_api_key)
deepseek_client = AsyncOpenAI(base_url=DEEPSEEK_BASE_URL, api_key=deepseek_api_key)
grok_client = AsyncOpenAI(base_url=GROK_BASE_URL, api_key=grok_api_key)
gemini_client = AsyncOpenAI(base_url=GEMINI_BASE_URL, api_key=google_api_key)


def get_model(model_name: str):
    if "/" in model_name:
        return OpenAIChatCompletionsModel(model=model_name, openai_client=openrouter_client)
    elif "deepseek" in model_name:
        return OpenAIChatCompletionsModel(model=model_name, openai_client=deepseek_client)
    elif "grok" in model_name:
        return OpenAIChatCompletionsModel(model=model_name, openai_client=grok_client)
    elif "gemini" in model_name:
        return OpenAIChatCompletionsModel(model=model_name, openai_client=gemini_client)
    else:
        return model_name


# Researcher agent:
# Step 1 in the overall flow is to build a "Researcher" Agent that knows how
# to talk to the research MCP servers (fetch, Brave search, memory).
async def get_researcher(mcp_servers, model_name) -> Agent:
    researcher = Agent(
        name="Researcher",
        instructions=researcher_instructions(),
        model=get_model(model_name),
        mcp_servers=mcp_servers,
    )
    return researcher

# Researcher tool:
# Step 2 is to expose the Researcher agent as a Tool that the Trader agent
# can call when it needs deeper market/news research.
async def get_researcher_tool(mcp_servers, model_name) -> Tool:
    researcher = await get_researcher(mcp_servers, model_name)
    return researcher.as_tool(tool_name="Researcher", tool_description=research_tool())


class Trader:
    """Single autonomous trader (e.g. Warren, George, Ray, Cathie).

    High-level flow (mirrors lab-4):
    0. Prepare MCP servers (trader + researcher).
    1. Build a Researcher agent (backed by research MCP servers).
    2. Wrap that Researcher as a tool.
    3. Build a Trader agent that can call the Researcher tool + trader MCP tools.
    4. Load the latest account + strategy.
    5. Send a trade or rebalance message and let Runner drive the conversation.

    In short:
    Trader.run() → Step 0: create MCP servers → Steps 1–5: build agents,
    load state, and let Runner.run handle the conversation.
    """
    def __init__(self, name: str, lastname="Trader", model_name="gpt-4o-mini"):
        self.name = name
        self.lastname = lastname
        self.agent = None
        self.model_name = model_name
        self.do_trade = True

    # Step 3: create the Trader agent that can call the Researcher tool and
    # also use the trader MCP servers (accounts, push, market).
    async def create_agent(self, trader_mcp_servers, researcher_mcp_servers) -> Agent:
        # 3a) Create the researcher tool (steps 1–2 above).
        tool = await get_researcher_tool(researcher_mcp_servers, self.model_name)
        # 3b) Create the trader agent with that tool attached.
        self.agent = Agent(
            name=self.name,
            instructions=trader_instructions(self.name),
            model=get_model(self.model_name),
            tools=[tool],
            mcp_servers=trader_mcp_servers,
        )
        return self.agent

    # Step 4: read the latest account snapshot for this trader via the
    # accounts MCP server (through accounts_client).
    async def get_account_report(self) -> str:
        account = await read_accounts_resource(self.name)
        account_json = json.loads(account)
        account_json.pop("portfolio_value_time_series", None)
        return json.dumps(account_json)

    # Step 5: wire everything together – create agent, load account + strategy,
    # build the initial message (trade vs rebalance), and run the dialog.
    async def run_agent(self, trader_mcp_servers, researcher_mcp_servers):

        self.agent = await self.create_agent(trader_mcp_servers, researcher_mcp_servers)
        account = await self.get_account_report()
        # 5b) Read the long-form strategy text from the strategy resource.
        strategy = await read_strategy_resource(self.name)
        # 5c) Build the initial user message the trader agent will respond to.
        message = (
            trade_message(self.name, strategy, account)
            if self.do_trade
            else rebalance_message(self.name, strategy, account)
        )
        # 5d) Let the Runner drive a multi-turn conversation with tools.
        await Runner.run(self.agent, message, max_turns=MAX_TURNS)
        # 5e) Clean up MCP servers when done. Good practice to avoid resource leaks.



    # Step 0 (from this class's perspective): prepare all MCP servers the
    # Trader and Researcher agents will use, then hand them into run_agent.
    # We follow the lab-4 pattern: construct MCPServerStdio instances directly
    # and let this run own them for its lifetime.
    async def run_with_mcp_servers(self):
        # Create MCP servers for trading actions (accounts, push, market).
        # 0a) Trader MCP servers (internal + external):
        #   - uv run accounts_server.py       (internal; talks to accounts.db)
        #   - uv run push_server.py           (internal; Pushover notifications)
        #   - Polygon mcp_polygon OR          (external; real market data)
        #     uv run market_server.py         (internal fallback market data)
        trader_mcp_servers = [
            MCPServerStdio(params, client_session_timeout_seconds=120)
            for params in trader_mcp_server_params
        ]

        # Create MCP servers for research (fetch, Brave search, memory).
        # 0b) Researcher MCP servers (all external-ish / sidecar services):
        #   - uvx mcp-server-fetch                    (generic HTTP fetch)
        #   - npx @modelcontextprotocol/server-brave-search (Brave Search API)
        #   - npx mcp-memory-libsql with ./memory/{name}.db (long-term memory)
        researcher_mcp_servers = [
            MCPServerStdio(params, client_session_timeout_seconds=120)
            for params in researcher_mcp_server_params(self.name)
        ]

        # 0c) Finally, run the trader agent using these MCP servers.
        await self.run_agent(trader_mcp_servers, researcher_mcp_servers)

    async def run_with_trace(self):
        # Wrap a single trader cycle in a named trace so we can inspect
        # tool calls and messages in the trace UI.
        trace_name = f"{self.name}-trading" if self.do_trade else f"{self.name}-rebalancing"
        trace_id = make_trace_id(f"{self.name.lower()}")
        with trace(trace_name, trace_id=trace_id):
            await self.run_with_mcp_servers()

    # Public entrypoint: one "tick" for this trader.
    # - Creates a trace
    # - Starts the MCP servers
    # - Runs trade or rebalance
    # - Flips do_trade for the next tick (trade → rebalance → trade → ...)
    async def run(self):
        try:
            await self.run_with_trace()
        except asyncio.CancelledError as e:
            print(f"Trader {self.name} was cancelled: {e}")
        except Exception as e:
            print(f"Error running trader {self.name}: {e}")
        self.do_trade = not self.do_trade


# ---------------------------------------------------------------------------
# Text flow diagram (per Trader.run)
#
#   trading_floor.py
#       ↳ Trader(name=...).run()   # "one tick" for a trader
#             ↳ run_with_trace()
#                   ↳ run_with_mcp_servers()   # Step 0: create MCP servers
#                         ↳ run_agent(trader_mcp_servers, researcher_mcp_servers)
#                               # Steps 1–5: build agents, load state,
#                               # and let Runner.run handle the conversation
#                               ↳ create_agent()            # builds Researcher tool + Trader agent
#                               ↳ get_account_report()      # via accounts MCP (internal)
#                               ↳ read_strategy_resource()  # via accounts MCP (strategy text)
#                               ↳ Runner.run(...)           # model + tools + MCP do the work
#
# MCP servers involved:
#   Trader MCP (internal + external market):
#     - accounts_server.py  (internal SQLite-backed accounts DB)
#     - push_server.py      (internal; Pushover notifications)
#     - mcp_polygon         (external Polygon MCP, if configured)
#       or market_server.py (internal fallback market data)
#
#   Researcher MCP (research + memory):
#     - mcp-server-fetch                        (HTTP fetch, external web)
#     - @modelcontextprotocol/server-brave-search (Brave Search API)
#     - mcp-memory-libsql with ./memory/{name}.db (per-trader long-term memory)
# ---------------------------------------------------------------------------
