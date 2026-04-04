import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class WeeklyPlanStore:
    """
    Stores the pending weekly plan proposal in memory/weekly_plan.json.
    The bot writes a proposal Sunday evening; the user approves with /approve.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except Exception:
            return {}

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2))

    def set_proposal(self, proposal: str) -> None:
        """Save a new pending proposal, overwriting any previous one."""
        self._save({"proposal": proposal, "pending": True})
        logger.info("Weekly plan proposal saved (%d chars).", len(proposal))

    def get_proposal(self) -> str | None:
        """Return the pending proposal text, or None if none is pending."""
        data = self._load()
        if data.get("pending"):
            return data.get("proposal")
        return None

    def approve(self) -> str | None:
        """
        Consume and return the pending proposal text.
        Returns None if there is no pending proposal.
        """
        data = self._load()
        if data.get("pending"):
            proposal = data.get("proposal", "")
            data["pending"] = False
            self._save(data)
            logger.info("Weekly plan proposal approved.")
            return proposal
        return None

    def clear(self) -> None:
        """Discard any pending proposal."""
        self._save({"pending": False})
