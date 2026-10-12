#!/usr/bin/env python3
import argparse
import asyncio
import csv
import re
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from env import load_env

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("DiscordScraper")


class ScraperConfig:
    _MODES: set[str] = {
        "chats",
        "media",
        "both"
    }

    def __init__(self, token: str, channel_id: str, mode: str, limit: int, output_dir: str):
        self.token = token
        self.channel_id = channel_id
        self.mode = mode.lower()
        self.limit = limit
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if self.mode not in ScraperConfig._MODES:
            raise ValueError(f"Mode must be one of: {', '.join(ScraperConfig._MODES)}")


class StateManager:
    def __init__(self, state_file: Path):
        self.state_file = state_file

    def load_state(self) -> Optional[str]:
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("oldest_message_id")
            except Exception as e:
                logger.warning(f"Failed to load state file: {e}")
        return None

    def save_state(self, oldest_message_id: str) -> None:
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump({"oldest_message_id": oldest_message_id}, f)
        except Exception as e:
            logger.error(f"Failed to save state file: {e}")


class CsvRepository:
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._initialize_file()

    def _initialize_file(self) -> None:
        file_exists = self.file_path.exists()
        if not file_exists:
            with open(self.file_path, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["message_id", "timestamp", "author_id", "author_name", "content", "attachments"])

    def append(self, message: Dict[str, Any], attachment_paths: List[str]) -> None:
        author = message.get("author", {})
        with open(self.file_path, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                message.get("id"),
                message.get("timestamp"),
                author.get("id"),
                author.get("username"),
                message.get("content", ""),
                ";".join(attachment_paths)
            ])


class DiscordApiClient:
    BASE_URL = "https://discord.com/api/v10"

    def __init__(self, token: str):
        self.headers = {
            "Authorization": token,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    async def fetch_messages(self, channel_id: str, before: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        url = f"{self.BASE_URL}/channels/{channel_id}/messages"
        params: dict[str, str | int] = {
            "limit": limit
        }

        if before:
            params["before"] = before

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=self.headers, params=params, timeout=30.0)

                if response.status_code == 429:
                    retry_after = response.json().get("retry_after", 2)
                    logger.warning(f"Rate limited. Sleeping for {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                    return await self.fetch_messages(channel_id, before, limit)

                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error occurred: {e.response.status_code} - {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Error fetching messages: {e}")
                raise

    async def download_media(self, url: str, destination: Path) -> bool:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, timeout=60.0)
                response.raise_for_status()

                destination = self.sanitize_and_cap_filename(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with open(destination, "wb") as f:
                    f.write(response.content)

                return True
            except Exception as e:
                logger.error(f"Failed to download media from {url}: {e}")
                return False


    @staticmethod
    def sanitize_and_cap_filename(filename: Path, max_length: int = 255) -> Path:
        """
        Sanitizes a filename by replacing unsafe characters with underscores,
        collapsing multiple spaces/underscores, and capping its length
        while preserving the file extension.
        """
        path = Path(filename) if not isinstance(filename, Path) else filename
        stem = path.stem
        suffix = path.suffix  # e.g., '.png', '.tar.gz'

        # 1. Replace invalid/unsafe characters (keep alphanumeric, dots, dashes, spaces, underscores)
        # This regex matches anything that is NOT a safe character and replaces it with '_'
        sanitized_stem = re.sub(r'[^\w\s\.-]', '_', stem)

        # 2. Collapse consecutive spaces or underscores into a single underscore for cleanliness
        sanitized_stem = re.sub(r'[\s_]+', '_', sanitized_stem).strip('_')

        # Fallback if the stem became completely empty
        if not sanitized_stem:
            sanitized_stem = "file"

        # 3. Calculate allowed length for the stem (Max length minus extension length)
        max_stem_length = max_length - len(suffix)

        if max_stem_length < 1:
            # Edge case: extension is longer than allowed max_length
            suffix = ""
            max_stem_length = max_length

        # 4. Cap the stem length
        capped_stem = sanitized_stem[:max_stem_length].rstrip('_.-')

        return path.with_name(f"{capped_stem}{suffix}")

class DiscordScraper:
    def __init__(
        self,
        config: ScraperConfig,
        api_client: DiscordApiClient,
        repository: CsvRepository,
        state_manager: StateManager
    ):
        self.config = config
        self.api = api_client
        self.repo = repository
        self.state = state_manager

    async def run(self) -> None:
        logger.info(f"Starting scraper for channel ID: {self.config.channel_id} in mode: [{self.config.mode}]")

        oldest_id = self.state.load_state()
        processed_count = 0
        batch_size = 100

        while True:
            if self.config.limit != -1 and processed_count >= self.config.limit:
                logger.info(f"Reached configured limit of {self.config.limit} items. Stopping.")
                break

            messages = await self.api.fetch_messages(self.config.channel_id, before=oldest_id, limit=batch_size)
            if not messages:
                logger.info("No more messages found in channel history.")
                break

            # Process messages chronologically (Discord returns newest first in pagination)
            for msg in messages:
                if self.config.limit != -1 and processed_count >= self.config.limit:
                    break

                oldest_id = msg["id"]
                attachments = msg.get("attachments", [])
                has_media = len(attachments) > 0
                has_text = bool(msg.get("content", "").strip())

                saved_attachments = []
                media_downloaded_in_msg = 0

                # Handle media download if required by mode
                if has_media and self.config.mode in {"media", "both"}:
                    media_dir = self.config.output_dir / "media" / self.config.channel_id

                    for att in attachments:
                        file_name = att.get("filename", "unknown_file")
                        file_url = att.get("url")
                        dest_path = media_dir / f"{msg['id']}_{file_name}"

                        logger.info(f"Downloading attachment: {file_name}")
                        success = await self.api.download_media(file_url, dest_path)

                        if success:
                            saved_attachments.append(str(dest_path))
                            media_downloaded_in_msg += 1

                            # Increment rule for media-only mode
                            if self.config.mode == "media":
                                processed_count += 1
                                if self.config.limit != -1 and processed_count >= self.config.limit:
                                    break

                # Save record to repository based on mode criteria
                should_save_chat = self.config.mode in {"chats", "both"} and (has_text or self.config.mode == "chats")
                should_save_media_record = self.config.mode == "media" and media_downloaded_in_msg > 0

                if should_save_chat or should_save_media_record:
                    self.repo.append(msg, saved_attachments)

                # Increment counters according to user specifications:
                # - Chats only: inc when each message received
                # - Both: inc when msg is either a message or media
                if self.config.mode == "chats":
                    processed_count += 1
                elif self.config.mode == "both":
                    if has_text or media_downloaded_in_msg > 0:
                        processed_count += 1

            # Save progress state after each batch chunk
            if oldest_id:
                self.state.save_state(oldest_id)

            # Avoid hitting API limits too fast
            await asyncio.sleep(0.5)

        logger.info(f"Scraping completed successfully. Total processed count: {processed_count}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discord Channel and DM Message Scraper via REST API.")

    parser.add_argument("--channel", "-c", required=True, help="Discord Channel ID or DM Channel ID")
    parser.add_argument("--mode", "-m", choices=["chats", "media", "both"], default="both", help="Scraping mode")
    parser.add_argument("--limit", "-l", type=int, default=-1, help="Limit item count (-1 for all content)")
    parser.add_argument("--output", "-o", default="./output", help="Output directory for CSV and downloaded media")

    return parser.parse_args()


async def main():
    args = parse_arguments()
    env = load_env()
    token = str(env.get("token") or env["TOKEN"]).strip()

    config = ScraperConfig(
        token=token,
        channel_id=args.channel,
        mode=args.mode,
        limit=args.limit,
        output_dir=args.output
    )

    output_csv = config.output_dir / f"discord_channel_{config.channel_id}.csv"
    state_file = config.output_dir / f"state_{config.channel_id}.json"

    api_client = DiscordApiClient(token=config.token)
    repository = CsvRepository(file_path=output_csv)
    state_manager = StateManager(state_file=state_file)

    scraper = DiscordScraper(
        config=config,
        api_client=api_client,
        repository=repository,
        state_manager=state_manager
    )

    try:
        await scraper.run()
    except KeyboardInterrupt:
        logger.info("Scraping interrupted by user. Progress state saved.")
    except Exception as e:
        logger.error(f"Fatal error during execution: {e}")


if __name__ == "__main__":
    asyncio.run(main())
