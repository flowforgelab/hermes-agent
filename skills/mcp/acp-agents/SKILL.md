---
name: acp-agents
description: Delegate tasks to external coding agents (Claude Code, Codex, Gemini, Cursor, and more) via the acp_agent tool. Supports one-shot exec and persistent session modes.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [ACP, Agents, Delegation, Claude, Codex, Gemini]
    related_skills: [native-mcp]
---

# ACP Coding Agents

Hermes can delegate tasks to external coding agents via the `acp_agent` tool. Each agent runs as an isolated process with its own capabilities, tools, and authentication — orchestrated through the Agent Client Protocol (ACP) via `acpx`.

## Available Agents

| Agent | Best for |
|-------|----------|
| `claude` | Deep reasoning, complex refactoring, architecture |
| `codex` | Fast code generation, OpenAI ecosystem |
| `gemini` | Broad knowledge, Google ecosystem |
| `copilot` | GitHub-integrated tasks |
| `cursor` | IDE-style code editing |
| `kiro` | AWS/cloud development |
| `kilocode` | Multi-provider gateway |
| `opencode` | Open-source focused |
| `kimi` | Strong reasoning, long context |
| `qwen` | Multilingual, Alibaba ecosystem |
| `cline` | VS Code extension agent |
| `amp` | Community agent |
| `droid` | Factory automation |
| `iflow` | Workflow automation |
| `pi` | Lightweight coding tasks |
| `trae` | ByteDance ecosystem |
| `qoder` | Code generation |

## Two Modes

### Exec (one-shot)
Use for isolated tasks with no follow-up needed.

```
acp_agent(agent="claude", task="Create a responsive landing page at ~/project/index.html")
acp_agent(agent="codex", task="Write unit tests for src/auth.py", cwd="/home/user/myapp")
```

### Session (persistent)
Use for multi-turn work where context matters. The session preserves conversation history across calls.

```
# First call creates the session
acp_agent(agent="claude", task="Review the auth module for security issues", mode="session", session_name="auth-review")

# Follow-up continues in the same session — Claude remembers the prior conversation
acp_agent(agent="claude", task="Now fix the SQL injection vulnerability you found", mode="session", session_name="auth-review")

# Another follow-up
acp_agent(agent="claude", task="Add regression tests for the fix", mode="session", session_name="auth-review")
```

## Working Directory

The `cwd` parameter controls where the agent operates. This matters because agents read/write files relative to this path.

```
# Work in a specific repo
acp_agent(agent="codex", task="Fix the failing tests", cwd="/home/user/repos/backend")

# Work in a temp directory
acp_agent(agent="claude", task="Build a demo app", cwd="/tmp/demo")
```

If omitted, defaults to the current Hermes workspace directory.

## Discord Thread Pattern

To start a coding agent in a Discord thread:

1. Create a thread (via `/thread` command or ask Hermes to create one)
2. Use `acp_agent` with `mode="session"` in the thread
3. Follow-up messages in the thread can continue the session by name

Example conversation:
> **User:** Start a Claude Code session called "refactor" and have it refactor the auth module in ~/repos/app
> **Hermes:** *(creates thread, calls acp_agent with mode=session, session_name="refactor")*
> **User:** Now have it add tests for the refactored code
> **Hermes:** *(calls acp_agent with same session_name="refactor" — Claude remembers the refactoring)*

## Timeout

Default timeout is 300 seconds (5 minutes). For longer tasks:

```
acp_agent(agent="claude", task="Full codebase review", timeout=600)
```

## Prerequisites

- **Node.js** must be installed (for `npx`)
- **acpx** is auto-installed via `npx` on first use, or install globally: `npm install -g acpx`
- Each agent needs its own authentication configured (API keys, OAuth tokens, etc.)

## Troubleshooting

| Error | Meaning | Fix |
|-------|---------|-----|
| "acpx not found" | Neither acpx nor npx is on PATH | Install Node.js and run `npm install -g acpx` |
| "Unknown agent" | Agent name not in the built-in registry | Check spelling, use one of the supported agent names |
| "Agent timed out" | Task took longer than timeout | Increase timeout or break task into smaller pieces |
| "Permission denied" | Agent requested a permission that was denied | This shouldn't happen with --approve-all; check agent config |
| "No session found" | Session mode without session_name | Always provide session_name with mode="session" |
| "auth failed" / "401" | Agent's credentials are missing or expired | Re-authenticate the specific agent (e.g., `claude auth login`, `codex auth`) |
