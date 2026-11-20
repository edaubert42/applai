"""A fictional LinkedIn MCP server over stdio, for tests: python linkedin_mcp_server.py JOBS CALLS.

JOBS is a JSON file mapping a job id to what get_job_details returns; an id it lacks makes
the tool fail. Every tool call is appended to CALLS, one JSON line each, before it runs.
Like the candidate's real server, it also has a tool that acts on LinkedIn.
"""

import json
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

jobs_file, calls_file = Path(sys.argv[1]), Path(sys.argv[2])
server = MCPServer("fictional-linkedin")


def _record(tool: str, arguments: dict[str, str]) -> None:
    with calls_file.open("a") as calls:
        calls.write(json.dumps({"tool": tool, "arguments": arguments}) + "\n")


@server.tool()
def get_job_details(job_id: str) -> str:
    _record("get_job_details", {"job_id": job_id})
    jobs = json.loads(jobs_file.read_text())
    if job_id not in jobs:
        raise ValueError(f"no job {job_id}")
    return json.dumps(jobs[job_id])


@server.tool()
def connect_with_person(profile_url: str) -> str:
    _record("connect_with_person", {"profile_url": profile_url})
    return "connection request sent"


if __name__ == "__main__":
    server.run("stdio")
