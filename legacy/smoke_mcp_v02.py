"""Exercise the recovered v0.2 server with its matching MCP SDK."""

import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    entry = Path(__file__).with_name("v0.2") / "server.py"
    with TemporaryDirectory(prefix="phonebridge-v02-") as directory:
        server = StdioServerParameters(command=sys.executable, args=[str(entry.resolve())], cwd=directory)
        async with stdio_client(server) as (source, destination):
            async with ClientSession(source, destination) as client:
                await client.initialize()
                tools = {tool.name for tool in (await client.list_tools()).tools}
                assert tools == {
                    "phone_status", "phone_observe", "phone_open_app", "phone_tap",
                    "phone_swipe", "phone_type_text", "phone_draft_message", "phone_confirm_message",
                }, tools

                async def call(name, **arguments):
                    result = await client.call_tool(name, arguments)
                    assert not result.isError, result
                    return json.loads(result.content[0].text)

                assert (await call("phone_status"))["real_device_connected"] is False
                assert (await call("phone_open_app", app_name="Bank"))["ok"] is False
                assert (await call("phone_open_app", app_name="WeChat"))["ok"] is True
                assert (await call("phone_observe"))["app"] == "WeChat"
                assert (await call("phone_tap", x=0.5, y=0.5))["simulated"] is True
                assert (await call("phone_swipe", start_x=0.5, start_y=0.8, end_x=0.5, end_y=0.2))["simulated"] is True
                assert (await call("phone_type_text", value="fixture"))["characters"] == 7
                draft = await call("phone_draft_message", recipient="Test Contact", message="fixture")
                assert draft["state"] == "awaiting_external_confirmation"
                blocked = await call("phone_confirm_message", approval_id=draft["approval_id"], approved=True)
                assert blocked["state"] == "blocked" and blocked["ok"] is False
                replay = await call("phone_confirm_message", approval_id=draft["approval_id"], approved=True)
                assert replay["error"] == "unknown_or_expired_approval"

    print("v0.2 MCP smoke passed: 8 tools discovered, simulated workflow, send blocked, replay rejected.")


if __name__ == "__main__":
    asyncio.run(main())
