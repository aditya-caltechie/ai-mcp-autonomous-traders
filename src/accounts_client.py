import mcp
from mcp.client.stdio import stdio_client
from mcp import StdioServerParameters
from agents import FunctionTool
import json

params = StdioServerParameters(command="uv", args=["run", "accounts_server.py"], env=None)


async def list_accounts_tools():
    async with stdio_client(params) as streams:
        async with mcp.ClientSession(*streams) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            return tools_result.tools
        
async def call_accounts_tool(tool_name, tool_args):
    async with stdio_client(params) as streams:
        async with mcp.ClientSession(*streams) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, tool_args)
            return result
            
# Read the full account JSON for a given trader name.
# This uses the same accounts_server.py MCP server, but reads from the
# accounts://accounts_server/{name} resource, which is backed by accounts.db.
# The server serializes the Account (from accounts.py) to JSON; we return
# that JSON string so callers can parse or trim fields as needed.
async def read_accounts_resource(name):
    async with stdio_client(params) as streams:
        async with mcp.ClientSession(*streams) as session:
            await session.initialize()
            result = await session.read_resource(f"accounts://accounts_server/{name}")
            return result.contents[0].text

# Read the long-form strategy text for a given trader name.
# This client talks to accounts_server.py by running `uv run accounts_server.py`
# over stdio. Inside, we:
#   - start the MCP server with stdio_client(params)
#   - wrap the streams in mcp.ClientSession and initialize the session
#   - call read_resource("accounts://strategy/{name}") to fetch the strategy
# The source of truth lives in accounts.db (via accounts.py/database.py);
# reset.py writes each trader's strategy there, and accounts_server.py exposes
# it as the accounts://strategy/{name} resource. We return that strategy string.
async def read_strategy_resource(name):
    async with stdio_client(params) as streams:
        async with mcp.ClientSession(*streams) as session:
            await session.initialize()
            result = await session.read_resource(f"accounts://strategy/{name}")
            return result.contents[0].text

async def get_accounts_tools_openai():
    openai_tools = []
    for tool in await list_accounts_tools():
        schema = {**tool.inputSchema, "additionalProperties": False}
        openai_tool = FunctionTool(
            name=tool.name,
            description=tool.description,
            params_json_schema=schema,
            on_invoke_tool=lambda ctx, args, toolname=tool.name: call_accounts_tool(toolname, json.loads(args))
                
        )
        openai_tools.append(openai_tool)
    return openai_tools