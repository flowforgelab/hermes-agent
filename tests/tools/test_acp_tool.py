"""Tests for the acp_agent tool."""

from __future__ import annotations

import json
import subprocess
from unittest import mock

import pytest

from tools.acp_tool import (
    KNOWN_AGENTS,
    _build_exec_command,
    _build_ensure_command,
    _build_prompt_command,
    _interpret_exit_code,
    _parse_ndjson_output,
    acp_agent_handler,
    check_acp_available,
)


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

class TestBuildExecCommand:
    def test_basic(self):
        with mock.patch("shutil.which", return_value="/usr/bin/acpx"):
            cmd = _build_exec_command("claude", "fix the bug", None, 300)
        assert cmd[0] == "acpx"
        assert "claude" in cmd
        assert "exec" in cmd
        assert "fix the bug" in cmd
        assert "--approve-all" in cmd
        assert "--format" in cmd
        assert "json" in cmd[cmd.index("--format") + 1]
        assert "--json-strict" in cmd
        assert "--timeout" in cmd
        assert "300" in cmd

    def test_with_cwd(self):
        with mock.patch("shutil.which", return_value="/usr/bin/acpx"):
            cmd = _build_exec_command("codex", "task", "/home/user/repo", 60)
        assert "--cwd" in cmd
        assert "/home/user/repo" in cmd

    def test_npx_fallback(self):
        with mock.patch("shutil.which", side_effect=lambda x: None if x == "acpx" else "/usr/bin/npx"):
            cmd = _build_exec_command("gemini", "task", None, 300)
        assert cmd[:3] == ["npx", "-y", "acpx"]


class TestBuildEnsureCommand:
    def test_basic(self):
        with mock.patch("shutil.which", return_value="/usr/bin/acpx"):
            cmd = _build_ensure_command("claude", "my-session", None)
        assert "sessions" in cmd
        assert "ensure" in cmd
        assert "--name" in cmd
        assert "my-session" in cmd

    def test_with_cwd(self):
        with mock.patch("shutil.which", return_value="/usr/bin/acpx"):
            cmd = _build_ensure_command("codex", "s1", "/tmp/repo")
        assert "--cwd" in cmd
        assert "/tmp/repo" in cmd


class TestBuildPromptCommand:
    def test_basic(self):
        with mock.patch("shutil.which", return_value="/usr/bin/acpx"):
            cmd = _build_prompt_command("claude", "do stuff", "my-session", None, 120)
        assert "-s" in cmd
        assert "my-session" in cmd
        assert "do stuff" in cmd
        assert "--approve-all" in cmd
        assert "--timeout" in cmd
        assert "120" in cmd


# ---------------------------------------------------------------------------
# NDJSON parsing
# ---------------------------------------------------------------------------

class TestParseNdjsonOutput:
    def test_agent_message_chunks(self):
        lines = "\n".join([
            json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
                "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hello "}}
            }}),
            json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
                "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "world!"}}
            }}),
        ])
        assert _parse_ndjson_output(lines) == "Hello world!"

    def test_ignores_non_text_events(self):
        lines = "\n".join([
            json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
                "update": {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "thinking..."}}
            }}),
            json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
                "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "answer"}}
            }}),
        ])
        assert _parse_ndjson_output(lines) == "answer"

    def test_ignores_tool_calls(self):
        lines = "\n".join([
            json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
                "update": {"sessionUpdate": "tool_call", "content": {"name": "read_file"}}
            }}),
            json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
                "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "done"}}
            }}),
        ])
        assert _parse_ndjson_output(lines) == "done"

    def test_ignores_result_messages(self):
        lines = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"stopReason": "end_turn"}})
        assert _parse_ndjson_output(lines) == ""

    def test_handles_malformed_lines(self):
        lines = "not json\n{\"method\": \"something\"}\n"
        assert _parse_ndjson_output(lines) == ""

    def test_empty_input(self):
        assert _parse_ndjson_output("") == ""


# ---------------------------------------------------------------------------
# Exit code interpretation
# ---------------------------------------------------------------------------

class TestInterpretExitCode:
    def test_error(self):
        result = _interpret_exit_code(1, "claude", "something broke", 300)
        assert result["status"] == "error"
        assert "something broke" in result["error"]
        assert result["agent"] == "claude"

    def test_timeout(self):
        result = _interpret_exit_code(3, "codex", "", 120)
        assert result["status"] == "timeout"
        assert "120s" in result["error"]

    def test_no_session(self):
        result = _interpret_exit_code(4, "gemini", "", 300)
        assert result["status"] == "error"
        assert "session" in result["error"].lower()

    def test_permission_denied(self):
        result = _interpret_exit_code(5, "claude", "", 300)
        assert result["status"] == "error"
        assert "permission" in result["error"].lower()

    def test_interrupted(self):
        result = _interpret_exit_code(130, "claude", "", 300)
        assert result["status"] == "interrupted"

    def test_unknown_code(self):
        result = _interpret_exit_code(42, "claude", "weird", 300)
        assert result["status"] == "error"
        assert "42" in result["error"]


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class TestAcpAgentHandler:
    def test_unknown_agent(self):
        result = json.loads(acp_agent_handler(agent="nonexistent", task="hi"))
        assert result["status"] == "error"
        assert "Unknown agent" in result["error"]

    def test_session_mode_requires_name(self):
        result = json.loads(acp_agent_handler(agent="claude", task="hi", mode="session"))
        assert result["status"] == "error"
        assert "session_name" in result["error"]

    def test_exec_mode_success(self):
        ndjson = json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
            "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Done!"}}
        }})
        mock_result = mock.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ndjson + "\n"
        mock_result.stderr = ""

        with mock.patch("subprocess.run", return_value=mock_result), \
             mock.patch("shutil.which", return_value="/usr/bin/acpx"):
            result = json.loads(acp_agent_handler(agent="claude", task="do it"))
        assert result["status"] == "completed"
        assert result["output"] == "Done!"
        assert result["agent"] == "claude"
        assert result["mode"] == "exec"

    def test_session_mode_success(self):
        ensure_result = mock.MagicMock()
        ensure_result.returncode = 0

        ndjson = json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
            "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Fixed it"}}
        }})
        prompt_result = mock.MagicMock()
        prompt_result.returncode = 0
        prompt_result.stdout = ndjson + "\n"
        prompt_result.stderr = ""

        with mock.patch("subprocess.run", side_effect=[ensure_result, prompt_result]), \
             mock.patch("shutil.which", return_value="/usr/bin/acpx"):
            result = json.loads(acp_agent_handler(
                agent="codex", task="fix tests", mode="session", session_name="bugfix",
            ))
        assert result["status"] == "completed"
        assert result["output"] == "Fixed it"
        assert result["session_name"] == "bugfix"

    def test_agent_error(self):
        mock_result = mock.MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "auth failed"

        with mock.patch("subprocess.run", return_value=mock_result), \
             mock.patch("shutil.which", return_value="/usr/bin/acpx"):
            result = json.loads(acp_agent_handler(agent="claude", task="hi"))
        assert result["status"] == "error"
        assert "auth failed" in result["error"]

    def test_timeout(self):
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("acpx", 300)), \
             mock.patch("shutil.which", return_value="/usr/bin/acpx"):
            result = json.loads(acp_agent_handler(agent="claude", task="hi", timeout=300))
        assert result["status"] == "timeout"

    def test_acpx_not_found(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError()), \
             mock.patch("shutil.which", return_value="/usr/bin/npx"):
            result = json.loads(acp_agent_handler(agent="claude", task="hi"))
        assert result["status"] == "error"
        assert "acpx not found" in result["error"]

    def test_plain_text_fallback(self):
        """If NDJSON parsing finds nothing, fall back to raw stdout."""
        mock_result = mock.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Plain text response\n"
        mock_result.stderr = ""

        with mock.patch("subprocess.run", return_value=mock_result), \
             mock.patch("shutil.which", return_value="/usr/bin/acpx"):
            result = json.loads(acp_agent_handler(agent="gemini", task="hi"))
        assert result["output"] == "Plain text response"


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

class TestAvailability:
    def test_acpx_on_path(self):
        with mock.patch("shutil.which", side_effect=lambda x: "/usr/bin/acpx" if x == "acpx" else None):
            assert check_acp_available() is True

    def test_npx_fallback(self):
        with mock.patch("shutil.which", side_effect=lambda x: "/usr/bin/npx" if x == "npx" else None):
            assert check_acp_available() is True

    def test_nothing_available(self):
        with mock.patch("shutil.which", return_value=None):
            assert check_acp_available() is False


# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------

class TestKnownAgents:
    def test_core_agents_present(self):
        for agent in ["claude", "codex", "gemini", "copilot", "cursor"]:
            assert agent in KNOWN_AGENTS

    def test_all_agents_lowercase(self):
        for agent in KNOWN_AGENTS:
            assert agent == agent.lower()
