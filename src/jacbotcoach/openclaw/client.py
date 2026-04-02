"""
OpenClaw HTTP client.

OpenClaw runs on localhost:18789 (loopback only).
Confirmed API endpoints (from /opt/homebrew/lib/node_modules/openclaw/dist/):

  GET  /api/v1/ping                 → health check
  GET  /api/v1/server/info          → server info (may include token/auth details)
  POST /api/v1/chat/new             → create a new chat session (sessions_spawn equivalent)
  POST /api/v1/message/text         → send a text message to a chat (sessions_send equivalent)
  GET  /api/v1/chat/query           → query/poll a chat session
  GET  /api/messages                → list messages

Authentication: token passed as query param ?token=<OPENCLAW_TOKEN>
Find your token via: curl http://localhost:18789/api/v1/server/info
"""

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_PING_PATH = "/api/v1/ping"
_SERVER_INFO_PATH = "/api/v1/server/info"
_CHAT_NEW_PATH = "/api/v1/chat/new"
_MESSAGE_TEXT_PATH = "/api/v1/message/text"
_CHAT_QUERY_PATH = "/api/v1/chat/query"


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
    Async HTTP client for OpenClaw's REST API (localhost:18789).

    Endpoint mapping:
      sessions_spawn  →  POST /api/v1/chat/new
      sessions_send   →  POST /api/v1/message/text
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
                    f"{self.base_url}{_PING_PATH}",
                    params=self._params(),
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def server_info(self) -> dict:
        """Fetch server info — useful for discovering the auth token."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{self.base_url}{_SERVER_INFO_PATH}",
                params=self._params(),
            )
            resp.raise_for_status()
            return resp.json()

    async def create_session(
        self,
        prompt: str,
        label: str = "",
    ) -> SessionResult:
        """
        Create a new OpenClaw chat session with an initial prompt.
        Equivalent to sessions_spawn.

        POST /api/v1/chat/new
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}{_CHAT_NEW_PATH}",
                params=self._params(),
                json={"text": prompt, "label": label},
            )
            resp.raise_for_status()
            data = resp.json()
            # Field names may vary — handle common variants
            session_id = (
                data.get("id")
                or data.get("chatId")
                or data.get("chat_id")
                or data.get("sessionId")
                or data.get("runId", "")
            )
            return SessionResult(
                session_id=str(session_id),
                status=data.get("status", "created"),
            )

    async def send_message(
        self,
        session_id: str,
        message: str,
    ) -> MessageResult:
        """
        Send a follow-up message to an existing OpenClaw chat session.
        Equivalent to sessions_send.

        POST /api/v1/message/text
        """
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}{_MESSAGE_TEXT_PATH}",
                params=self._params(),
                json={"chatId": session_id, "text": message},
            )
            resp.raise_for_status()
            data = resp.json()
            return MessageResult(
                session_id=session_id,
                response=data.get("response") or data.get("text", ""),
                status=data.get("status", "sent"),
            )

    async def query_session(self, session_id: str) -> dict:
        """Poll the status/output of a chat session."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self.base_url}{_CHAT_QUERY_PATH}",
                params={**self._params(), "chatId": session_id},
            )
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json()
