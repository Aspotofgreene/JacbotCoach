import json
import logging
import re
import httpx

logger = logging.getLogger(__name__)


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks that qwen3 and similar models emit."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


class OllamaClient:
    """HTTP client for a single Ollama instance."""

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def generate(self, prompt: str, timeout: float = 180.0) -> str:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self.base_url}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False},
            )
            resp.raise_for_status()
            return _strip_thinking(resp.json()["response"])

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False


def extract_json_list(text: str) -> list[str]:
    """Pull the first JSON array out of an LLM response."""
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if not match:
        # Fall back: treat each non-empty line as a task
        return [
            line.lstrip("•-*0123456789.) ").strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    try:
        items = json.loads(match.group())
        return [str(i).strip() for i in items if str(i).strip()]
    except json.JSONDecodeError:
        return []
