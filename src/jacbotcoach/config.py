from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str
    telegram_allowed_user_id: int

    # Ollama — Mac mini (light tasks)
    ollama_mac_url: str = "http://localhost:11434"
    ollama_mac_model: str = "llama3:14b"

    # Ollama — Desktop GPU (heavy tasks)
    ollama_desktop_url: str = "http://192.168.50.206:11434"
    ollama_desktop_model: str = "deepseek-r1:14b"

    # OpenClaw
    openclaw_url: str = "http://localhost:18789"
    openclaw_token: str = ""

    # Scheduler
    daily_task_hour: int = 8
    daily_task_minute: int = 0
    morning_brief_hour: int = 6
    evening_reflection_hour: int = 20
    stall_days: int = 7
    timezone: str = "America/New_York"

    # File paths
    autonomous_md_path: Path = Path("AUTONOMOUS.md")
    tasks_log_path: Path = Path("memory/tasks-log.md")
    streaks_path: Path = Path("memory/streaks.json")
    milestones_path: Path = Path("memory/milestones.md")
    research_queue_path: Path = Path("memory/research-queue.json")
    research_dir: Path = Path("research")
    ideas_path: Path = Path("memory/ideas.md")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
