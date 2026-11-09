from pathlib import Path
from dataclasses import dataclass, asdict
from json import dump as json_dump, load as json_load

from utils import get_logger

logger = get_logger("StateManager")


@dataclass(frozen=False)
class ScraperState:
    oldest_message_id: int


class StateManager:
    def __init__(self, state_file: Path):
        self.state_file = state_file

    def load_state(self) -> ScraperState:
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                return ScraperState(
                    oldest_message_id=json_load(f).get("oldest_message_id")
                )
        except Exception as e:
            logger.warning(f"Failed to load state file: {e}")

        return ScraperState(oldest_message_id=-1)

    def save_state(self, state: ScraperState) -> None:
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json_dump(asdict(state), f)
        except Exception as e:
            logger.error(f"Failed to save state file: {e}")


__all__ = ("StateManager",)
