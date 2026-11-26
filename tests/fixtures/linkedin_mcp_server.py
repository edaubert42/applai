"""A fictional LinkedIn MCP server over stdio, for tests: linkedin_mcp_server.py JOBS CALLS [STARTS].

JOBS is a JSON file mapping a job id to what get_job_details returns; an id it lacks makes
the tool fail. Every tool call is appended to CALLS, one JSON line each, before it runs.
Each start appends the server's process id to STARTS, when given, one line each.
Like the candidate's real server, it also has a tool that acts on LinkedIn.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, cast

from mcp.server.mcpserver import MCPServer
from mcp.types import TextContent

jobs_file, calls_file = Path(sys.argv[1]), Path(sys.argv[2])
starts_file = Path(sys.argv[3]) if len(sys.argv) > 3 else None
server = MCPServer("fictional-linkedin")


def _record(tool: str, arguments: dict[str, str]) -> None:
    with calls_file.open("a") as calls:
        calls.write(json.dumps({"tool": tool, "arguments": arguments}) + "\n")


@server.tool()
def get_job_details(job_id: str) -> str | list[TextContent]:
    _record("get_job_details", {"job_id": job_id})
    jobs: dict[str, Any] = json.loads(jobs_file.read_text())
    if job_id not in jobs:
        raise ValueError(f"no job {job_id}")
    payload: Any = jobs[job_id]
    if isinstance(payload, dict) and "_extra_blocks" in payload:
        raw = cast(dict[str, Any], payload)
        job = {k: v for k, v in raw.items() if k != "_extra_blocks"}
        extras = cast(list[str], raw["_extra_blocks"])
        return [
            TextContent(type="text", text=json.dumps(job)),
            *(TextContent(type="text", text=text) for text in extras),
        ]
    if isinstance(payload, str):
        return payload
    return json.dumps(payload)


@server.tool()
def connect_with_person(profile_url: str) -> str:
    _record("connect_with_person", {"profile_url": profile_url})
    return "connection request sent"


if __name__ == "__main__":
    if starts_file:
        with starts_file.open("a") as starts:
            starts.write(f"{os.getpid()}\n")
    server.run("stdio")
