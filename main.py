#!/usr/bin/env python3
import argparse
import asyncio
from pathlib import Path
from typing import Optional

from client import DiscordApiClient
from env import load_env
from utils import get_logger
from repository import SaveRepository, REPOSITORY_MAP
from state import StateManager


logger = get_logger("DiscordScraper")


class ScraperConfig:
    _MODES: set[str] = {"chats", "media", "both"}

    def __init__(
        self, token: str, channel_id: str, mode: str, limit: int, output_dir: str
    ):
        self.token = token
        self.channel_id = channel_id
        self.mode = mode.lower()
        self.limit = limit
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if self.mode not in ScraperConfig._MODES:
            raise ValueError(f"Mode must be one of: {', '.join(ScraperConfig._MODES)}")


class DiscordScraper:
    def __init__(
        self,
        config: ScraperConfig,
        api_client: DiscordApiClient,
        repository: SaveRepository,
        state_manager: StateManager,
    ):
        self.config = config
        self.api = api_client
        self.save_repo = repository
        self.state_manager = state_manager

    async def run(self) -> None:
        logger.info(
            f"Starting scraper for channel ID: {self.config.channel_id} in mode: [{self.config.mode}]"
        )

        state = self.state_manager.load_state()
        processed_count = 0
        batch_size = 100

        logger.debug(f"Scraping state: {state}")
        while True:
            if self.config.limit != -1 and processed_count >= self.config.limit:
                logger.info(
                    f"Reached configured limit of {self.config.limit} items. Stopping."
                )
                break

            messages = await self.api.fetch_messages(
                channel_id=self.config.channel_id,
                before=state.oldest_message_id,
                limit=batch_size,
            )

            if not messages:
                logger.info("No more messages found in channel history.")
                break

            oldest_id: Optional[str | int] = None
            # Process messages chronologically (Discord returns newest first in pagination)
            for msg in messages:
                if self.config.limit != -1 and processed_count >= self.config.limit:
                    break

                oldest_id = int(msg["id"])
                attachments = msg.get("attachments", [])
                has_media = len(attachments) > 0
                has_text = bool(msg.get("content", "").strip())

                saved_attachments = []
                media_downloaded_in_msg = 0

                # Handle media download if required by mode
                if has_media and self.config.mode in {"media", "both"}:
                    media_dir = (
                        self.config.output_dir / "media" / self.config.channel_id
                    )

                    for att in attachments:
                        file_name = att.get("filename", "unknown_file")
                        file_url = att.get("url")
                        dest_path = media_dir / f"{msg['id']}_{file_name}"

                        logger.info(f"Downloading attachment: {file_name}")
                        success = await self.api.download_media(file_url, dest_path)

                        if success:
                            saved_attachments.append(
                                str(dest_path.expanduser().resolve().absolute())
                            )
                            media_downloaded_in_msg += 1

                            # Increment rule for media-only mode
                            if self.config.mode == "media":
                                processed_count += 1
                                if (
                                    self.config.limit != -1
                                    and processed_count >= self.config.limit
                                ):
                                    break

                # Save record to repository based on mode criteria
                should_save_chat = self.config.mode in {"chats", "both"} and (
                    has_text or self.config.mode == "chats"
                )
                should_save_media_record = (
                    self.config.mode == "media" and media_downloaded_in_msg > 0
                )

                if should_save_chat or should_save_media_record:
                    self.save_repo.append(msg, saved_attachments)

                # Increment counters according to user specifications:
                # - Chats only: inc when each message received
                # - Both: inc when msg is either a message or media
                if self.config.mode == "chats":
                    processed_count += 1
                elif self.config.mode == "both":
                    if has_text or media_downloaded_in_msg > 0:
                        processed_count += 1

            # Save progress state after each batch chunk
            if (
                type(oldest_id) is int
                and oldest_id > 0
                or type(oldest_id) is str
                and oldest_id.strip()
            ):
                state.oldest_message_id = int(oldest_id)
                self.state_manager.save_state(state)

            # Avoid hitting API limits too fast
            await asyncio.sleep(0.5)

        logger.info(
            f"Scraping completed successfully. Total processed count: {processed_count}"
        )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discord Channel and DM Message Scraper via REST API."
    )

    parser.add_argument("channel", help="Discord Channel ID or DM Channel ID")
    parser.add_argument(
        "output_mode",
        choices=REPOSITORY_MAP.keys(),
        help=f"Output mode to use when storing info",
    )

    parser.add_argument(
        "--limit",
        type=int, default=-1,
        help="Limit item count (-1 for all content)"
    )
    parser.add_argument(
        "--output",
        default="./output",
        help="Output directory for info and downloaded media",
    )
    parser.add_argument(
        "--mode", "-m",
        choices=ScraperConfig._MODES, default="both",
        help="Scraping mode",
    )

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
        output_dir=args.output,
    )

    config.output_dir.mkdir(exist_ok=True, parents=True)
    output_mode = str(args.output_mode).lower().lstrip(".")

    output_file = (
        config.output_dir / f"discord_channel_{config.channel_id}.{output_mode}"
    )
    state_file = config.output_dir / f"state_{config.channel_id}.json"

    api_client = DiscordApiClient(token=config.token)
    state_manager = StateManager(state_file=state_file)
    repository = REPOSITORY_MAP[output_mode](output_file)

    scraper = DiscordScraper(
        config=config,
        api_client=api_client,
        repository=repository,
        state_manager=state_manager,
    )

    try:
        with repository:
            await scraper.run()
    except KeyboardInterrupt:
        logger.info("Scraping interrupted by user. Progress state saved.")
    except Exception as e:
        logger.error(f"Fatal error during execution: {e}")


if __name__ == "__main__":
    asyncio.run(main())
