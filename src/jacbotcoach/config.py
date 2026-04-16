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
    research_dir: Path = Path.home() / "research"
    ideas_path: Path = Path("memory/ideas.md")
    weekly_plan_path: Path = Path("memory/weekly_plan.json")
    draft_queue_path: Path = Path("memory/draft-queue.json")
    drafts_dir: Path = Path.home() / "drafts"
    project_queue_path: Path = Path("memory/project-queue.json")
    projects_dir: Path = Path.home() / "projects"
    project_builder_hour: int = 2  # 2 AM daily
    weekly_planning_hour: int = 18  # 6 PM Sunday
    accountability_scoring_hour: int = 19  # 7 PM Sunday

    # Accountability Scoring
    accountability_scores_path: Path = Path("memory/accountability-scores.json")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
