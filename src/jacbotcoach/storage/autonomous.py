import re
from pathlib import Path

AUTONOMOUS_MAX_LINES = 50


class AutonomousStore:
    """
    Manages AUTONOMOUS.md — the shared goals + backlog file.

    Rules:
    - Only the main session (JacbotCoach bot) writes here.
    - Sub-agents (OpenClaw sessions) NEVER touch this file.
    - Hard cap of AUTONOMOUS_MAX_LINES to keep it token-light.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8")

    def write(self, content: str) -> None:
        lines = content.strip().splitlines()
        if len(lines) > AUTONOMOUS_MAX_LINES:
            raise ValueError(
                f"AUTONOMOUS.md would be {len(lines)} lines "
                f"(max {AUTONOMOUS_MAX_LINES}). Summarize before saving."
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(content.strip() + "\n", encoding="utf-8")

    def line_count(self) -> int:
        return len(self.read().splitlines())

    def is_empty(self) -> bool:
        content = self.read().strip()
        return not content or "(empty" in content

    def mark_goal_done(self, goal_title: str) -> bool:
        """
        Find a goal row containing goal_title and move it to ## Completed.
        Returns True if found and moved, False if not found.
        """
        content = self.read()
        lines = content.splitlines()
        found_row = None
        remaining_lines = []

        for line in lines:
            if (
                line.strip().startswith("|")
                and goal_title.lower() in line.lower()
                and not all(c in "-| " for c in line.strip())
                and not any(h in line for h in ("Status", "Difficulty", "Frequency", "Goal", "Habit"))
            ):
                found_row = line.strip()
                # Replace Active/In Progress/Paused status with Done
                found_row = re.sub(
                    r"\|\s*(Active|In Progress|Paused)\s*\|",
                    "| Done |",
                    found_row,
                )
            else:
                remaining_lines.append(line)

        if not found_row:
            return False

        # Append to ## Completed section, or create it
        result = "\n".join(remaining_lines)
        if "## Completed" in result:
            result = result.replace(
                "## Completed\n(empty)",
                f"## Completed\n{found_row}",
            )
            if found_row not in result:
                result = re.sub(
                    r"(## Completed\n)",
                    f"\\1{found_row}\n",
                    result,
                )
        else:
            result = result.rstrip() + f"\n\n## Completed\n{found_row}\n"

        self.write(result)
        return True

    def promote_backlog_item(
        self,
        item_text: str,
        category: str,
        difficulty: str,
        rephrased: str,
        notes: str,
    ) -> bool:
        """
        Remove item_text from ## Open Backlog and add it to the given category table.
        Returns True if the backlog item was found and moved.
        """
        content = self.read()

        # Remove from backlog
        new_content = re.sub(
            r"^\s*-\s+" + re.escape(item_text) + r"\s*$",
            "",
            content,
            flags=re.MULTILINE,
        )
        if new_content == content:
            return False  # item not found in backlog

        # Build new table row
        new_row = f"| Active | {difficulty} | {rephrased} | {notes} |"

        # Insert into the correct category section
        section_map = {
            "Short Term Projects": "## Projects — Short Term",
            "Long Term Projects": "## Projects — Long Term",
            "Habits & Ongoing": "## Habits & Ongoing",
        }
        section_header = section_map.get(category, "## Projects — Short Term")

        if section_header in new_content:
            # Find the last row of that section's table and insert after it
            lines = new_content.splitlines()
            insert_after = -1
            in_section = False
            for i, line in enumerate(lines):
                if section_header in line:
                    in_section = True
                elif in_section and line.startswith("## "):
                    break
                elif in_section and line.strip().startswith("|"):
                    insert_after = i

            if insert_after >= 0:
                lines.insert(insert_after + 1, new_row)
                new_content = "\n".join(lines)

        self.write(new_content)
        return True

    def update_goal_status(self, goal_title: str, status: str) -> bool:
        """
        Update the Status column of a goal row.
        Status: Active | In Progress | Paused | Done
        """
        content = self.read()
        lines = content.splitlines()
        updated = False

        for i, line in enumerate(lines):
            if (
                line.strip().startswith("|")
                and goal_title.lower() in line.lower()
                and not all(c in "-| " for c in line.strip())
                and not any(h in line for h in ("Status", "Difficulty", "Frequency", "Goal", "Habit"))
            ):
                lines[i] = re.sub(
                    r"\|\s*(Active|In Progress|Paused|Done)\s*\|",
                    f"| {status} |",
                    line,
                )
                updated = True
                break

        if updated:
            self.write("\n".join(lines))
        return updated
