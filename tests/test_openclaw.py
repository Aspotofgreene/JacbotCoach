import pytest
import respx
import httpx

from jacbotcoach.openclaw.client import OpenClawClient


BASE = "http://localhost:18789"


class TestOpenClawClient:
    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_ok(self):
        respx.get(f"{BASE}/health").mock(
            return_value=httpx.Response(200, json={"ok": True, "status": "live"})
        )
        client = OpenClawClient(BASE, token="test-token")
        assert await client.health_check() is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_unreachable(self):
        respx.get(f"{BASE}/health").mock(side_effect=httpx.ConnectError("refused"))
        client = OpenClawClient(BASE, token="test-token")
        assert await client.health_check() is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_session(self):
        respx.post(f"{BASE}/sessions").mock(
            return_value=httpx.Response(
                200,
                json={"id": "sess-abc123", "status": "created"},
            )
        )
        client = OpenClawClient(BASE, token="test-token")
        result = await client.create_session("Do some research", label="Research task")
        assert result.session_id == "sess-abc123"
        assert result.status == "created"

    @pytest.mark.asyncio
    @respx.mock
    async def test_send_message(self):
        respx.post(f"{BASE}/sessions/sess-abc123/messages").mock(
            return_value=httpx.Response(
                200,
                json={"response": "Done.", "status": "sent"},
            )
        )
        client = OpenClawClient(BASE, token="test-token")
        result = await client.send_message("sess-abc123", "Continue the task")
        assert result.response == "Done."
        assert result.status == "sent"

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_session_404_raises_helpful_error(self):
        respx.post(f"{BASE}/sessions").mock(
            return_value=httpx.Response(404, text="Not Found")
        )
        client = OpenClawClient(BASE, token="test-token")
        with pytest.raises(RuntimeError, match="sessions endpoint not found"):
            await client.create_session("Some task")

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_sessions(self):
        respx.get(f"{BASE}/sessions").mock(
            return_value=httpx.Response(
                200,
                json=[{"id": "s1", "label": "task1"}, {"id": "s2", "label": "task2"}],
            )
        )
        client = OpenClawClient(BASE)
        sessions = await client.list_sessions()
        assert len(sessions) == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_token_appended_as_query_param(self):
        route = respx.post(f"{BASE}/sessions").mock(
            return_value=httpx.Response(200, json={"id": "s1", "status": "created"})
        )
        client = OpenClawClient(BASE, token="mytoken")
        await client.create_session("task")
        assert "token=mytoken" in str(route.calls[0].request.url)
