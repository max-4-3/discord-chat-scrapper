from pathlib import Path
from functools import reduce

import re, logging


def get_logger(name: str) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )

    return logging.getLogger(name)


def sanitize_and_cap_filename(filename: Path, max_length: int = 255) -> Path:
    """
    Sanitizes a filename by replacing unsafe characters with underscores,
    collapsing multiple spaces/underscores, and capping its length
    while preserving the file extension."""

    path = Path(filename) if not isinstance(filename, Path) else filename
    stem, suffix = path.stem, path.suffix

    repl_rex = [
        ("_", r"[^\w\s\.-]"),  # invalid/unsafe characters
        ("_", r"[\s_]+"),  # consecutive spaces or underscores
        ("", r"^_$"),  # single underscore
    ]

    sanitized_stem = (
        reduce(lambda ss, rpl: re.sub(rpl[1], rpl[0], ss), repl_rex, stem) or "file"
    )

    # 3. Calculate allowed length for the stem (Max length minus extension length)
    max_stem_length = max_length - len(suffix)

    if max_stem_length < 1:
        # Edge case: extension is longer than allowed max_length
        suffix = ""
        max_stem_length = max_length

    # 4. Cap the stem length
    capped_stem = sanitized_stem[:max_stem_length].rstrip("_.-")

    return path.with_name(f"{capped_stem}{suffix}")


__all__ = ("sanitize_and_cap_filename", "get_logger")
