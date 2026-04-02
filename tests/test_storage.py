import asyncio
import pytest
from pathlib import Path

from jacbotcoach.storage.autonomous import AutonomousStore, AUTONOMOUS_MAX_LINES
from jacbotcoach.storage.tasks_log import TasksLog


class TestAutonomousStore:
    def test_write_and_read(self, tmp_path):
        store = AutonomousStore(tmp_path / "AUTONOMOUS.md")
        store.write("# Goals\n- Goal one\n- Goal two")
        assert "Goal one" in store.read()

    def test_50_line_guard_passes(self, tmp_path):
        store = AutonomousStore(tmp_path / "AUTONOMOUS.md")
        content = "\n".join(f"- Goal {i}" for i in range(AUTONOMOUS_MAX_LINES))
        store.write(content)  # should not raise

    def test_50_line_guard_fails(self, tmp_path):
        store = AutonomousStore(tmp_path / "AUTONOMOUS.md")
        content = "\n".join(f"- Goal {i}" for i in range(AUTONOMOUS_MAX_LINES + 1))
        with pytest.raises(ValueError, match="exceed"):
            store.write(content)

    def test_is_empty_on_new_file(self, tmp_path):
        store = AutonomousStore(tmp_path / "AUTONOMOUS.md")
        assert store.is_empty()

    def test_is_empty_with_placeholder(self, tmp_path):
        store = AutonomousStore(tmp_path / "AUTONOMOUS.md")
        store.path.write_text("# Goals\n(empty — use /goals)")
        assert store.is_empty()

    def test_line_count(self, tmp_path):
        store = AutonomousStore(tmp_path / "AUTONOMOUS.md")
        store.write("line 1\nline 2\nline 3")
        assert store.line_count() == 3


class TestTasksLog:
    @pytest.mark.asyncio
    async def test_append_and_read_today(self, tmp_path):
        log = TasksLog(tmp_path / "tasks-log.md")
        await log.append("Research competitors", status="SCHEDULED")
        today_tasks = log.read_today()
        assert len(today_tasks) == 1
        assert "Research competitors" in today_tasks[0]
        assert "SCHEDULED" in today_tasks[0]

    @pytest.mark.asyncio
    async def test_append_is_only_additive(self, tmp_path):
        log = TasksLog(tmp_path / "tasks-log.md")
        await log.append("Task A")
        await log.append("Task B")
        lines = [l for l in log.read_all().splitlines() if l.strip().startswith("-")]
        assert len(lines) == 2

    @pytest.mark.asyncio
    async def test_concurrent_appends(self, tmp_path):
        log = TasksLog(tmp_path / "tasks-log.md")
        await asyncio.gather(
            log.append("Concurrent task 1"),
            log.append("Concurrent task 2"),
            log.append("Concurrent task 3"),
        )
        lines = [l for l in log.read_all().splitlines() if l.strip().startswith("-")]
        assert len(lines) == 3

    @pytest.mark.asyncio
    async def test_read_today_empty(self, tmp_path):
        log = TasksLog(tmp_path / "tasks-log.md")
        assert log.read_today() == []
