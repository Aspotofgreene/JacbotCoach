"""
OpenClaw integration.

OpenClaw runs as 'openclaw-gateway' on localhost:18789 (loopback only).
Installed at: /opt/homebrew/lib/node_modules/openclaw/  (v2026.4.1)

Integration strategy:
  PRIMARY:  CLI subprocess — `openclaw agent --message "..." --json`
            No HTTP auth required. Uses stored credentials from ~/.openclaw/
            Credentials and sessions live in ~/.openclaw/agents/main/

  FALLBACK: HTTP API — /api/channels requires a token (returns Unauthorized).
            Token location: ~/.openclaw/openclaw.json or identity/device-auth.json

Confirmed CLI flags (from `openclaw agent --help`):
  --message <text>        The prompt / task to run         ← main flag
  --session-id <id>       Target an existing session       ← for follow-ups
  --thinking <level>      off|minimal|low|medium|high|xhigh
  --timeout <seconds>     Default 600s
  --json                  Structured JSON output
  --agent <id>            Use a specific agent (default: main)
  --deliver               Send reply back to the configured channel
"""

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_HEALTH_PATH = "/health"
_OPENCLAW_BIN = "openclaw"  # on PATH via /opt/homebrew/bin/openclaw


@dataclass
class SessionResult:
    session_id: str
    status: str
    output: str = ""


@dataclass
class MessageResult:
    session_id: str
    response: str
    status: str


class OpenClawClient:
    """
    OpenClaw integration using the CLI subprocess approach.

    `openclaw agent` runs one autonomous agent turn through the gateway,
    which is the equivalent of sessions_spawn + running the task to completion.

    Falls back to HTTP if the CLI is unavailable.
    """

    def __init__(self, base_url: str, token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._bin = shutil.which(_OPENCLAW_BIN) or _OPENCLAW_BIN

    # ------------------------------------------------------------------ #
    # Health                                                               #
    # ------------------------------------------------------------------ #

    async def health_check(self) -> bool:
        """Check both HTTP gateway and CLI availability."""
        http_ok = await self._http_health()
        cli_ok = await self._cli_available()
        logger.info("OpenClaw health — HTTP: %s, CLI: %s", http_ok, cli_ok)
        return http_ok or cli_ok

    async def _http_health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}{_HEALTH_PATH}")
                data = resp.json()
                return data.get("ok") is True or data.get("status") == "live"
        except Exception:
            return False

    async def _cli_available(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._bin, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5.0)
            return proc.returncode == 0
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Session creation (primary path)                                     #
    # ------------------------------------------------------------------ #

    async def create_session(
        self,
        prompt: str,
        label: str = "",
        working_dir: str | None = None,
        thinking: str = "high",
    ) -> SessionResult:
        """
        Spawn an autonomous OpenClaw agent task.

        Uses `openclaw agent --message "<prompt>" --thinking high --json`.
        The process runs in the background (default timeout 600s in OpenClaw).
        The prompt should instruct the agent to append a ✅ line to
        memory/tasks-log.md when done.
        """
        import json as _json
        import re as _re
        from datetime import datetime

        cmd = self._build_agent_cmd(prompt, thinking)
        logger.info("Spawning OpenClaw agent: label=%r thinking=%s", label, thinking)

        # First, try a quick launch to grab the session ID from JSON output.
        # We wait up to 10s. If OpenClaw responds quickly (short tasks or
        # immediate acknowledgement), great. Otherwise we re-launch with
        # DEVNULL pipes so the long-running process isn't killed by a broken
        # pipe when Python GCs the proc handle.
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=10.0
            )
            raw = stdout.decode(errors="replace").strip()
            err_raw = stderr.decode(errors="replace").strip()
            combined = (raw + "\n" + err_raw).lower()

            # Detect known fatal errors regardless of exit code (openclaw
            # sometimes exits 0 even on auth/pairing failures)
            for fatal in ("pairing required", "gateway closed", "unauthorized", "not authenticated"):
                if fatal in combined:
                    raise RuntimeError(
                        f"openclaw agent auth error ({fatal!r}). "
                        "Run 'openclaw pair' or re-authorize via the OpenClaw app."
                    )

            if proc.returncode != 0:
                raise RuntimeError(
                    f"openclaw agent failed (exit {proc.returncode}): {err_raw or raw}"
                )
            session_id = _extract_session_id(raw) or _slugify(label) or f"task-{datetime.now().strftime('%H%M%S')}"
            logger.info("OpenClaw session completed quickly: %s", session_id)
            return SessionResult(session_id=session_id, status="running", output=raw)

        except asyncio.TimeoutError:
            # Task is still running. Kill the pipe-connected proc and re-spawn
            # with DEVNULL so the subprocess won't get SIGPIPE when Python
            # closes its end of the pipes after this function returns.
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass

            bg_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=working_dir,
            )
            session_id = _slugify(label) or f"pid-{bg_proc.pid}"
            logger.info(
                "OpenClaw agent running in background (pid=%d, id=%s)",
                bg_proc.pid,
                session_id,
            )
            return SessionResult(session_id=session_id, status="running", output=f"pid={bg_proc.pid}")

    def _build_agent_cmd(self, prompt: str, thinking: str = "high", agent: str = "main") -> list[str]:
        """Build the confirmed `openclaw agent` command."""
        return [
            self._bin, "agent",
            "--agent", agent,
            "--message", prompt,
            "--thinking", thinking,
            "--json",
        ]

    # ------------------------------------------------------------------ #
    # Send follow-up message (secondary path)                             #
    # ------------------------------------------------------------------ #

    async def send_message(
        self,
        session_id: str,
        message: str,
        thinking: str = "medium",
    ) -> MessageResult:
        """
        Send a follow-up message to an existing OpenClaw session via CLI.

        Uses: openclaw agent --session-id <id> --message "<text>" --json
        """
        cmd = [
            self._bin, "agent",
            "--session-id", session_id,
            "--message", message,
            "--thinking", thinking,
            "--json",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            raw = stdout.decode(errors="replace").strip()
            if proc.returncode != 0:
                err = stderr.decode(errors="replace").strip()
                raise RuntimeError(f"openclaw agent --session-id failed: {err or raw}")
            return MessageResult(session_id=session_id, response=raw, status="sent")
        except asyncio.TimeoutError:
            return MessageResult(session_id=session_id, response="(timeout)", status="timeout")

    async def list_sessions(self) -> list[dict]:
        """List sessions via CLI: openclaw agents list."""
        try:
            proc = await asyncio.create_subprocess_exec(
                self._bin, "agents", "list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            raw = stdout.decode(errors="replace").strip()
            # Output is a table — just return lines as dicts for now
            return [{"raw": line} for line in raw.splitlines() if line.strip()]
        except Exception as e:
            logger.debug("list_sessions failed: %s", e)
            return []


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #

def _extract_session_id(json_output: str) -> str:
    """Pull session ID from openclaw agent --json output."""
    import json, re
    # Try JSON parse first
    try:
        data = json.loads(json_output)
        return str(
            data.get("sessionId")
            or data.get("session_id")
            or data.get("id")
            or ""
        )
    except Exception:
        pass
    # Fallback: regex scan
    match = re.search(r'"(?:sessionId|session_id|id)"\s*:\s*"([^"]+)"', json_output)
    return match.group(1) if match else ""


def _slugify(text: str) -> str:
    """Convert label text to a safe session ID slug."""
    import re
    return re.sub(r"[^a-z0-9-]", "-", text.lower())[:40].strip("-")
