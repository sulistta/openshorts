import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_server_initializes_over_stdio_without_legacy_tools(tmp_path: Path):
    async def run() -> tuple[str, set[str]]:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "openshorts_mcp"],
            cwd=str(Path(__file__).parents[1]),
            env={**os.environ, "OPENSHORTS_OUTPUT_DIR": str(tmp_path / "output")},
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                return initialized.serverInfo.name, {tool.name for tool in tools.tools}

    name, tools = asyncio.run(run())

    assert name == "OpenShorts"
    assert "render_clips" in tools
    assert "get_contact_sheet" in tools
    assert "process_video" not in tools
    assert "publish_clip" not in tools
