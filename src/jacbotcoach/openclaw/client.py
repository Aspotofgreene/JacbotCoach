"""
OpenClaw integration.

OpenClaw runs as 'openclaw-gateway' on localhost:18789 (loopback only).
Installed at: /opt/homebrew/lib/node_modules/openclaw/

Integration strategy:
  PRIMARY:  CLI subprocess — `openclaw agent` runs one agent turn via the gateway.
            No HTTP auth required. Works immediately.

  FALLBACK: HTTP API — /api/channels (and others) exist but require a token.
            Token location: ~/.openclaw/ (check with `ls ~/.openclaw/`)

CLI usage (confirmed from `openclaw --help`):
  openclaw agent          Run one agent turn via the Gateway
  openclaw agents *       Manage isolated agents

HTTP API (needs token, passed as ?token=<value>):
  GET  /health            → {"ok": true, "status": "live"}
  GET  /api/channels      → Unauthorized without token (endpoint exists)

TODO: Run `openclaw agent --help` and paste output to finalize CLI params.
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
    ) -> SessionResult:
        """
        Run an autonomous agent task via `openclaw agent`.

        This spawns an OpenClaw agent that executes the given prompt as one
        complete agent turn through the local gateway.

        TODO: Run `openclaw agent --help` to confirm the exact flag names.
              Common patterns for similar tools:
                openclaw agent --prompt "..." --session-key my-task
                openclaw agent "..." --workspace /path/to/dir
        """
        cmd = self._build_agent_cmd(prompt, label, working_dir)
        logger.info("Spawning OpenClaw agent: %s", " ".join(cmd[:3]) + " ...")

        # Run in background — don't wait for completion (tasks can take hours)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )

        # Give it 3s to fail fast (e.g. auth error, bad args)
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=3.0
            )
            if proc.returncode is not None and proc.returncode != 0:
                err = stderr.decode(errors="replace").strip()
                raise RuntimeError(
                    f"openclaw agent exited with code {proc.returncode}: {err}"
                )
            output = stdout.decode(errors="replace").strip()
        except asyncio.TimeoutError:
            # Still running — that's expected for long tasks
            output = f"agent running (pid {proc.pid})"
            logger.info("OpenClaw agent running in background (pid %d)", proc.pid)

        session_id = label.replace(" ", "-")[:40] or f"pid-{proc.pid}"
        return SessionResult(
            session_id=session_id,
            status="running",
            output=output,
        )

    def _build_agent_cmd(
        self,
        prompt: str,
        label: str,
        working_dir: str | None,
    ) -> list[str]:
        """
        Build the `openclaw agent` command.

        TODO: Update these flags once `openclaw agent --help` output is known.
              Replace the placeholder flag names with the real ones.
        """
        cmd = [self._bin, "agent"]

        # Common flag patterns — update after running `openclaw agent --help`
        # Option A: positional prompt
        cmd.append(prompt)

        # Option B: --prompt flag (uncomment if needed)
        # cmd += ["--prompt", prompt]

        if label:
            # Try --session-key or --label (update after checking --help)
            cmd += ["--session-key", label[:40]]

        if self._token:
            cmd += ["--token", self._token]

        return cmd

    # ------------------------------------------------------------------ #
    # Send follow-up message (secondary path)                             #
    # ------------------------------------------------------------------ #

    async def send_message(
        self,
        session_id: str,
        message: str,
    ) -> MessageResult:
        """
        Send a follow-up message to an existing session via HTTP API.
        Requires the auth token to be set.

        TODO: Confirm the correct endpoint after getting the auth token.
        """
        if not self._token:
            logger.warning(
                "send_message called without token — "
                "set OPENCLAW_TOKEN in .env to enable HTTP API"
            )
            return MessageResult(
                session_id=session_id,
                response="(token not set — HTTP API unavailable)",
                status="skipped",
            )

        params = {"token": self._token}
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Try the most likely endpoint paths
            for path in [
                f"/api/channels/message",
                f"/api/messages",
            ]:
                try:
                    resp = await client.post(
                        f"{self.base_url}{path}",
                        params=params,
                        json={"sessionId": session_id, "text": message},
                    )
                    if resp.status_code != 404:
                        resp.raise_for_status()
                        data = resp.json()
                        return MessageResult(
                            session_id=session_id,
                            response=data.get("response") or data.get("text", ""),
                            status=data.get("status", "sent"),
                        )
                except httpx.HTTPStatusError:
                    continue

        return MessageResult(
            session_id=session_id,
            response="(send_message: no working endpoint found)",
            status="unknown",
        )

    async def list_sessions(self) -> list[dict]:
        """List sessions via HTTP API (requires token)."""
        if not self._token:
            return []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.base_url}/api/channels",
                    params={"token": self._token},
                )
                if resp.status_code in (401, 403):
                    logger.warning("OpenClaw token rejected — check OPENCLAW_TOKEN in .env")
                    return []
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, list) else data.get("channels", [])
        except Exception as e:
            logger.debug("list_sessions failed: %s", e)
            return []
