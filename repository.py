from abc import ABC, abstractmethod
import json
from pathlib import Path
from csv import writer as csv_writer, reader as csv_reader
from typing import Callable, Protocol, Any, Dict, Self

from utils import get_logger

logger = get_logger("SaveRepository")

type JsonValue = (
    str | int | bool | None | float | list[JsonValue] | dict[str, JsonValue]
)
CSV_HEADERS = [
    "message_id",
    "timestamp",
    "author_id",
    "author_name",
    "content",
    "attachments",
]


class SaveRepository(Protocol):
    def __init__(self, path: Path): ...
    def __enter__(self) -> Self: ...
    def __exit__(self, exc_type, exc_val, exc_tb) -> bool | None: ...

    def append(self, message_data: Dict[str, Any], attachment_paths: list[str]) -> None: ...


class BaseSaveRepository[T](ABC):
    def __init__(self, path: Path) -> None:
        self.file = path
        self.data: list[T] = self.load(self.file)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool | None:
        return self.save(self.file, self.data)

    def append(self, message_data: Dict[str, Any], attachment_paths: list[str]) -> None:
        self.data.append(self.transform(message_data, attachment_paths))
        logger.debug(
            f"Added message_data (prev = {len(self.data) - 1}; now = {len(self.data)})"
        )

    @staticmethod
    @abstractmethod
    def save(file_path: Path, data: list[T]) -> None: ...

    @staticmethod
    @abstractmethod
    def load(file_path: Path) -> list[T]: ...

    @staticmethod
    @abstractmethod
    def transform(message_data: Dict[str, Any], attachment_paths: list[str]) -> T: ...


class CsvSaveRepository(BaseSaveRepository[list[str]]):
    @staticmethod
    def load(file_path: Path) -> list[list[str]]:
        result = []

        if file_path.exists():
            with file_path.open("r", newline="", encoding="utf-8") as file:
                reader = csv_reader(file)
                headers = next(reader, None)

                if headers != CSV_HEADERS:
                    raise ValueError(
                        f"Invalid CSV headers in {file_path}, not: {CSV_HEADERS}"
                    )

                result = list(reader)

            logger.info(f"CSV: loaded {len(result)} existing messages")

        return result

    @staticmethod
    def save(file_path: Path, data: list[list[str]]) -> None:
        with file_path.open("w", newline="", encoding="utf-8") as file:
            logger.info(f"CSV: writing {len(data) + 1} lines")

            writer = csv_writer(file)
            writer.writerow(CSV_HEADERS)
            writer.writerows(data)

    @staticmethod
    def transform(
        message_data: dict[str, Any], attachment_paths: list[str]
    ) -> list[str]:
        author = message_data.get("author") or {}

        result = [
            message_data.get("id"),             # message_id
            message_data.get("timestamp"),      # timestamp
            author.get("id"),                   # author_id
            author.get("username"),             # author_name
            message_data.get("content", ""),    # content
            ";".join(attachment_paths),         # attachments
        ]

        return list(map(str, result))


class JsonSaveRepository(BaseSaveRepository[dict[str, JsonValue]]):
    @staticmethod
    def load(file_path: Path) -> list[dict[str, JsonValue]]:
        result = []

        if file_path.exists():
            with file_path.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
                if not (isinstance(loaded, list) and
                        all(map(lambda m: type(m) is dict, loaded))):
                    raise ValueError(f"Invalid JSON messages structure in {file_path}")

                result = loaded

            logger.info(f"JSON: loaded {len(result)} existing messages")

        return result

    @staticmethod
    def save(file_path: Path, data: list[dict[str, JsonValue]]) -> None:
        with file_path.open("w", encoding="utf-8") as file:
            logger.info(f"JSON: writing array of {len(data)} messages")

            json.dump(list(data), file, indent=2, ensure_ascii=False)

    @staticmethod
    def transform(message_data: dict[str, Any], attachment_paths: list[str]) -> dict[str, JsonValue]:
        author = message_data.get("author") or {}
        return {
            "message_id": message_data.get("id"),
            "timestamp": message_data.get("timestamp"),
            "author_id": author.get("id"),
            "author_name": author.get("username"),
            "content": message_data.get("content"),
            "attachments": list(attachment_paths),
        }


REPOSITORY_MAP: dict[str, Callable[[Path], SaveRepository]] = {
    "csv": CsvSaveRepository,
    "json": JsonSaveRepository,
}
__all__ = ("REPOSITORY_MAP", "SaveRepository")
