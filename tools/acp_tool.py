"""ACP Agent Tool — delegate tasks to external coding agents via acpx.

Spawns ``acpx <agent> exec|prompt`` as a subprocess and returns the
agent's response.  All ACP protocol handling, authentication, and
session management is delegated to acpx.

Two modes:
  exec    — one-shot task, no persistent state
  session — named session with multi-turn context preservation
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Agents recognised by acpx's built-in registry.
KNOWN_AGENTS = frozenset({
    "claude", "codex", "gemini", "copilot", "cursor", "kiro",
    "kilocode", "opencode", "kimi", "qwen", "cline", "amp",
    "droid", "iflow", "pi", "trae", "qoder",
})

_DEFAULT_TIMEOUT = 300  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_acpx() -> Optional[str]:
    """Return the path to acpx (or npx as fallback), or None."""
    return shutil.which("acpx") or shutil.which("npx")


def _build_exec_command(
    agent: str,
    task: str,
    cwd: Optional[str],
    timeout: int,
) -> List[str]:
    """Build argv for a one-shot exec invocation."""
    cmd = _acpx_base()
    cmd += [agent, "exec", task,
            "--approve-all", "--format", "json", "--json-strict"]
    if cwd:
        cmd += ["--cwd", cwd]
    cmd += ["--timeout", str(timeout)]
    return cmd


def _build_ensure_command(
    agent: str,
    session_name: str,
    cwd: Optional[str],
) -> List[str]:
    """Build argv to ensure a named session exists (idempotent)."""
    cmd = _acpx_base()
    cmd += [agent, "sessions", "ensure", "--name", session_name]
    if cwd:
        cmd += ["--cwd", cwd]
    return cmd


def _build_prompt_command(
    agent: str,
    task: str,
    session_name: str,
    cwd: Optional[str],
    timeout: int,
) -> List[str]:
    """Build argv for a session-mode prompt."""
    cmd = _acpx_base()
    cmd += [agent, "-s", session_name, task,
            "--approve-all", "--format", "json", "--json-strict"]
    if cwd:
        cmd += ["--cwd", cwd]
    cmd += ["--timeout", str(timeout)]
    return cmd


def _acpx_base() -> List[str]:
    """Return the base command prefix for acpx."""
    if shutil.which("acpx"):
        return ["acpx"]
    return ["npx", "-y", "acpx"]


def _parse_ndjson_output(stdout: str) -> str:
    """Extract agent message text from NDJSON (raw ACP JSON-RPC) lines."""
    parts: List[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        # ACP session update events carry the agent's streaming text
        if msg.get("method") == "session/update":
            params = msg.get("params") or {}
            update = params.get("update") or {}
            kind = update.get("sessionUpdate") or ""
            if kind == "agent_message_chunk":
                content = update.get("content") or {}
                text = content.get("text", "") if isinstance(content, dict) else ""
                if text:
                    parts.append(text)
    return "".join(parts).strip()


def _interpret_exit_code(code: int, agent: str, stderr: str, timeout: int) -> Dict[str, Any]:
    """Map acpx exit code to a structured error response."""
    messages = {
        1: f"Agent/protocol error: {stderr}" if stderr else "Agent or protocol error",
        2: f"CLI usage error: {stderr}" if stderr else "Invalid acpx command",
        3: f"Agent timed out after {timeout}s",
        4: "No session found. Use mode='session' with a session_name.",
        5: "Permission denied by agent",
        130: "Agent was interrupted",
    }
    status = {1: "error", 2: "error", 3: "timeout", 4: "error", 5: "error", 130: "interrupted"}
    return {
        "agent": agent,
        "status": status.get(code, "error"),
        "error": messages.get(code, f"acpx exited with code {code}: {stderr}"),
        "exit_code": code,
    }


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def acp_agent_handler(
    agent: str,
    task: str,
    mode: str = "exec",
    session_name: Optional[str] = None,
    cwd: Optional[str] = None,
    timeout: Optional[int] = None,
    task_id: Optional[str] = None,
) -> str:
    """Execute an ACP agent task via acpx and return the result as JSON."""
    agent = agent.strip().lower()
    timeout = timeout or _DEFAULT_TIMEOUT

    if agent not in KNOWN_AGENTS:
        return json.dumps({
            "agent": agent,
            "status": "error",
            "error": f"Unknown agent '{agent}'. Choose from: {', '.join(sorted(KNOWN_AGENTS))}",
        })

    if mode == "session" and not session_name:
        return json.dumps({
            "agent": agent,
            "status": "error",
            "error": "session_name is required when mode='session'",
        })

    effective_cwd = cwd or os.environ.get("TERMINAL_CWD") or os.getcwd()

    try:
        if mode == "session":
            # Step 1: ensure session exists (idempotent)
            ensure_cmd = _build_ensure_command(agent, session_name, effective_cwd)
            logger.debug("ACP ensure: %s", " ".join(ensure_cmd))
            ensure_result = subprocess.run(
                ensure_cmd, capture_output=True, text=True, timeout=30,
            )
            if ensure_result.returncode not in (0,):
                stderr = ensure_result.stderr.strip()
                return json.dumps({
                    "agent": agent,
                    "status": "error",
                    "error": f"Failed to ensure session '{session_name}': {stderr}",
                    "exit_code": ensure_result.returncode,
                })

            # Step 2: send prompt to session
            cmd = _build_prompt_command(agent, task, session_name, effective_cwd, timeout)
        else:
            cmd = _build_exec_command(agent, task, effective_cwd, timeout)

        logger.debug("ACP run: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 30,  # safety margin over acpx's own timeout
            cwd=effective_cwd,
        )

    except subprocess.TimeoutExpired:
        return json.dumps({
            "agent": agent,
            "status": "timeout",
            "error": f"Agent timed out after {timeout}s (Python-level safety timeout)",
            "exit_code": -1,
        })
    except FileNotFoundError:
        return json.dumps({
            "agent": agent,
            "status": "error",
            "error": "acpx not found. Install with: npm install -g acpx",
            "exit_code": -1,
        })

    if result.returncode != 0:
        return json.dumps(_interpret_exit_code(
            result.returncode, agent, result.stderr.strip(), timeout,
        ))

    output = _parse_ndjson_output(result.stdout)
    if not output and result.stdout.strip():
        # Fallback: maybe acpx printed plain text (text format or error)
        output = result.stdout.strip()

    response: Dict[str, Any] = {
        "agent": agent,
        "status": "completed",
        "mode": mode,
        "output": output,
        "exit_code": 0,
    }
    if session_name:
        response["session_name"] = session_name
    return json.dumps(response)


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def check_acp_available() -> bool:
    """Return True if acpx (or npx) is available on PATH."""
    return _find_acpx() is not None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

ACP_AGENT_SCHEMA = {
    "name": "acp_agent",
    "description": (
        "Delegate a task to an external coding agent (Claude Code, Codex, "
        "Gemini, Cursor, etc.) via ACP. Use 'exec' for one-shot tasks, "
        "'session' for persistent multi-turn work.\n\n"
        "WHEN TO USE:\n"
        "- Coding tasks that benefit from a dedicated agent (debugging, "
        "refactoring, file creation)\n"
        "- When you want a specific agent's capabilities (Claude's reasoning, "
        "Codex's speed, Gemini's breadth)\n"
        "- Multi-step work in a named session (follow-ups preserve context)\n\n"
        "WHEN NOT TO USE:\n"
        "- Simple file reads/writes — use file tools directly\n"
        "- Shell commands — use terminal directly\n"
        "- Tasks needing your conversation context — the agent starts fresh"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "enum": sorted(KNOWN_AGENTS),
                "description": "Which coding agent to use",
            },
            "task": {
                "type": "string",
                "description": (
                    "What the agent should accomplish. Be specific and "
                    "self-contained — the agent has no context from this "
                    "conversation."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["exec", "session"],
                "description": (
                    "exec = one-shot, no state. session = persistent, "
                    "reuse session_name for follow-ups. Default: exec"
                ),
            },
            "session_name": {
                "type": "string",
                "description": (
                    "Named session for multi-turn work (mode=session only). "
                    "Reuse the same name to continue a conversation."
                ),
            },
            "cwd": {
                "type": "string",
                "description": (
                    "Working directory for the agent. Defaults to the "
                    "current workspace."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to wait (default: 300).",
                "minimum": 10,
            },
        },
        "required": ["agent", "task"],
    },
}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

from tools.registry import registry

registry.register(
    name="acp_agent",
    toolset="delegation",
    schema=ACP_AGENT_SCHEMA,
    handler=lambda args, **kw: acp_agent_handler(
        agent=args.get("agent", ""),
        task=args.get("task", ""),
        mode=args.get("mode", "exec"),
        session_name=args.get("session_name"),
        cwd=args.get("cwd"),
        timeout=args.get("timeout"),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_acp_available,
    emoji="🔀",
)
