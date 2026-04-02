"""
OpenClaw HTTP client.

OpenClaw runs on localhost:18789 (loopback only).
API discovery notes:
  GET  /health                     → {"ok": true, "status": "live"}
  POST /sessions                   → create a new session (returns session ID)
  POST /sessions/{id}/messages     → send a message to a session

TODO: Verify the exact session endpoint paths by checking:
  1. The OpenClaw web UI at http://localhost:18789 → Settings → API
  2. Or run: curl http://localhost:18789/sessions
             curl http://localhost:18789/v1/sessions
  3. The token is in the OpenClaw UI under Settings → API Token

The client is designed so only this file changes when the exact paths are confirmed.
"""

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Known-working API paths (update if needed after checking the OpenClaw UI)
_SESSIONS_PATH = "/sessions"
_MESSAGES_SUFFIX = "/messages"
_HEALTH_PATH = "/health"


@dataclass
class SessionResult:
    session_id: str
    status: str


@dataclass
class MessageResult:
    session_id: str
    response: str
    status: str


class OpenClawClient:
    """
    Thin async HTTP wrapper around OpenClaw's session API.

    All calls go to localhost:18789 (loopback).
    Auth: token is appended as ?token=<OPENCLAW_TOKEN> query param.
    """

    def __init__(self, base_url: str, token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token

    def _params(self) -> dict:
        return {"token": self._token} if self._token else {}

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self.base_url}{_HEALTH_PATH}",
                    params=self._params(),
                )
                data = resp.json()
                return data.get("ok") is True or data.get("status") == "live"
        except Exception:
            return False

    async def create_session(
        self,
        prompt: str,
        label: str = "",
    ) -> SessionResult:
        """
        Spawn a new OpenClaw session with an initial prompt.

        This is the HTTP equivalent of sessions_spawn — creates an isolated
        session and starts it with the given prompt as the first message.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}{_SESSIONS_PATH}",
                params=self._params(),
                json={"prompt": prompt, "label": label},
            )
            if resp.status_code == 404:
                raise RuntimeError(
                    f"OpenClaw sessions endpoint not found at {_SESSIONS_PATH}. "
                    "Check the correct path in the OpenClaw UI under Settings → API "
                    "and update _SESSIONS_PATH in openclaw/client.py."
                )
            resp.raise_for_status()
            data = resp.json()
            session_id = data.get("id") or data.get("session_id") or data.get("runId", "")
            return SessionResult(session_id=str(session_id), status=data.get("status", "created"))

    async def send_message(
        self,
        session_id: str,
        message: str,
    ) -> MessageResult:
        """
        Send a follow-up message to an existing OpenClaw session.
        Equivalent to sessions_send.
        """
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}{_SESSIONS_PATH}/{session_id}{_MESSAGES_SUFFIX}",
                params=self._params(),
                json={"message": message},
            )
            resp.raise_for_status()
            data = resp.json()
            return MessageResult(
                session_id=session_id,
                response=data.get("response", ""),
                status=data.get("status", "sent"),
            )

    async def list_sessions(self) -> list[dict]:
        """List all active OpenClaw sessions (useful for debugging)."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self.base_url}{_SESSIONS_PATH}",
                params=self._params(),
            )
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("sessions", [])
