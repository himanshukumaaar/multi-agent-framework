import os
import sys
import json
import asyncio
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from observability.context import get_trace_id
from observability.tracer import ObservabilityTracer


def _default_sidecar_python() -> str:
    repo_root = Path(__file__).resolve().parent.parent
    candidates = [
        repo_root / ".venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "bin" / "python",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return sys.executable


MCP_TOOLS_ENABLED = os.getenv("MCP_TOOLS_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
MCP_TOOL_SERVER_COMMAND = os.getenv("MCP_TOOL_SERVER_COMMAND", _default_sidecar_python()).strip()
MCP_TOOL_SERVER_SCRIPT = os.getenv(
    "MCP_TOOL_SERVER_SCRIPT",
    str((Path(__file__).resolve().parent / "mcp_tool_server.py")),
).strip()
MCP_TOOL_SERVER_ARGS = os.getenv("MCP_TOOL_SERVER_ARGS", "").strip()
MCP_BRIDGE_COMMAND = os.getenv("MCP_BRIDGE_COMMAND", MCP_TOOL_SERVER_COMMAND).strip()
MCP_BRIDGE_SCRIPT = os.getenv(
    "MCP_BRIDGE_SCRIPT",
    str((Path(__file__).resolve().parent / "mcp_bridge_client.py")),
).strip()
MCP_CALL_TIMEOUT_SECONDS = int(os.getenv("MCP_CALL_TIMEOUT_SECONDS", "20"))


async def _call_mcp_tool_via_bridge(
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[str | None, str | None]:
    start_dt = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    success = True
    error_type = ""

    bridge_path = Path(MCP_BRIDGE_SCRIPT)
    if not bridge_path.exists():
        err = f"MCP bridge script not found: {bridge_path}"
        ObservabilityTracer.record_mcp_tool_call(
            trace_id=get_trace_id(),
            tool_name=tool_name,
            start_time=start_dt,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            success=False,
            error_type="ScriptNotFound",
            input_metadata=arguments,
            output_metadata={"error": err}
        )
        return None, err

    payload = json.dumps(arguments, ensure_ascii=False)
    cmd = [
        MCP_BRIDGE_COMMAND,
        str(bridge_path),
        "--tool-name",
        tool_name,
        "--arguments-json",
        payload,
        "--server-command",
        MCP_TOOL_SERVER_COMMAND,
        "--server-script",
        MCP_TOOL_SERVER_SCRIPT,
        "--server-args",
        MCP_TOOL_SERVER_ARGS,
    ]

    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=MCP_CALL_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        err = f"MCP tool call timed out after {MCP_CALL_TIMEOUT_SECONDS}s ({tool_name})"
        ObservabilityTracer.record_mcp_tool_call(
            trace_id=get_trace_id(),
            tool_name=tool_name,
            start_time=start_dt,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            success=False,
            error_type="TimeoutExpired",
            input_metadata=arguments,
            output_metadata={"error": err}
        )
        return None, err
    except Exception as e:
        err = f"MCP bridge spawn failed: {e}"
        ObservabilityTracer.record_mcp_tool_call(
            trace_id=get_trace_id(),
            tool_name=tool_name,
            start_time=start_dt,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            success=False,
            error_type=type(e).__name__,
            input_metadata=arguments,
            output_metadata={"error": err}
        )
        return None, err

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()

    if completed.returncode != 0:
        detail = stderr or stdout or f"exit code {completed.returncode}"
        err = f"MCP bridge failed ({tool_name}): {detail}"
        ObservabilityTracer.record_mcp_tool_call(
            trace_id=get_trace_id(),
            tool_name=tool_name,
            start_time=start_dt,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            success=False,
            error_type="NonZeroExitCode",
            input_metadata=arguments,
            output_metadata={"error": err}
        )
        return None, err

    if not stdout:
        err = f"MCP bridge returned empty output ({tool_name})"
        ObservabilityTracer.record_mcp_tool_call(
            trace_id=get_trace_id(),
            tool_name=tool_name,
            start_time=start_dt,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            success=False,
            error_type="EmptyOutput",
            input_metadata=arguments,
            output_metadata={"error": err}
        )
        return None, err

    result = None
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            result = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if result is None:
        err = f"Failed to parse MCP bridge output JSON ({tool_name})"
        ObservabilityTracer.record_mcp_tool_call(
            trace_id=get_trace_id(),
            tool_name=tool_name,
            start_time=start_dt,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            success=False,
            error_type="JSONDecodeError",
            input_metadata=arguments,
            output_metadata={"error": err}
        )
        return None, err

    t1 = time.perf_counter()
    ObservabilityTracer.record_mcp_tool_call(
        trace_id=get_trace_id(),
        tool_name=tool_name,
        start_time=start_dt,
        duration_ms=(t1 - t0) * 1000.0,
        success=True,
        input_metadata=arguments,
        output_metadata={"has_result": bool(result)}
    )

    if result.get("ok"):
        return str(result.get("result", "")).strip(), None

    error_text = str(result.get("error", "")).strip() or f"Unknown MCP bridge error ({tool_name})"
    return None, error_text


async def _call_mcp_tool(tool_name: str, arguments: dict[str, Any]) -> tuple[str | None, str | None]:
    if not MCP_TOOLS_ENABLED:
        return None, "MCP tools are disabled."

    script_path = Path(MCP_TOOL_SERVER_SCRIPT)
    if not script_path.exists():
        return None, f"MCP tool server script not found: {script_path}"

    if not MCP_TOOL_SERVER_COMMAND:
        return None, "MCP tool server command is empty."
    if not MCP_BRIDGE_COMMAND:
        return None, "MCP bridge command is empty."

    return await _call_mcp_tool_via_bridge(tool_name, arguments)


async def mcp_web_search(
    query: str,
    max_results: int = 5,
    recency_days: int | None = None,
    relevance_query: str | None = None,
) -> tuple[str | None, str | None]:
    return await _call_mcp_tool(
        "web_search",
        {
            "query": query,
            "max_results": int(max_results),
            "recency_days": recency_days,
            "relevance_query": relevance_query,
        },
    )


async def mcp_calculator(expression: str) -> tuple[str | None, str | None]:
    return await _call_mcp_tool("calculator", {"expression": expression})
