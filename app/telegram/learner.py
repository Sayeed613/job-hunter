"""Learned profile — persists user answers to unknown dropdown fields.

When the bot asks the user via Telegram for a dropdown selection, the
answer is saved here. Next time the same field is encountered, the
bot uses the saved answer instead of asking again.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("job_automation_bot")


class LearnedProfile:
    """Persists user answers to form fields for future auto-fill.

    Uses a simple JSON file on disk. Each entry maps a field identifier
    (the field hint or matched_field key) to the selected option text.

    Usage:
        profile = LearnedProfile()
        answer = profile.get("gender")
        if answer:
            # Use the saved answer
        else:
            # Ask user, then save:
            profile.set("gender", "Male")
    """

    def __init__(self, path: str | Path = "storage/learned_profile.json") -> None:
        self._path = Path(path)
        self._data: dict[str, str] = self._load()

    # ── Public API ───────────────────────────────────────────

    def get(self, field_key: str) -> Optional[str]:
        """Get the saved answer for a field.

        Args:
            field_key: The field identifier (hint text or matched field key).

        Returns:
            The saved option text, or None if not learned yet.
        """
        return self._data.get(field_key)

    def set(self, field_key: str, answer: str) -> None:
        """Save the answer for a field so it's auto-filled next time.

        Args:
            field_key: The field identifier.
            answer: The selected option text.
        """
        self._data[field_key] = answer
        self._save()
        logger.info("Learned: '%s' → '%s'", field_key, answer[:40])

    def get_all(self) -> dict[str, str]:
        """Return all learned answers (read-only)."""
        return dict(self._data)

    # ── Persistence ──────────────────────────────────────────

    def _load(self) -> dict[str, str]:
        if self._path.exists():
            try:
                raw = self._path.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict):
                    return {k: str(v) for k, v in data.items()}
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load learned profile: %s", e)
        return {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Failed to save learned profile: %s", e)

    def __len__(self) -> int:
        return len(self._data)
