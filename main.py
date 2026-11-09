#!/usr/bin/env python3
import argparse
import asyncio
from pathlib import Path
from typing import Awaitable

from client import DiscordApiClient
from env import load_env
from utils import get_logger
from repository import SaveRepository, REPOSITORY_MAP
from state import StateManager


logger = get_logger("DiscordScraper")


class ScraperConfig:
    _MODES: set[str] = {"chats", "media", "both"}

    def __init__(
        self, token: str, channel_id: str, mode: str, limit: int, output_dir: str, delay: float, ntasks: int,
    ):
        self.token = token
        self.channel_id = channel_id
        self.mode = mode.lower()
        self.limit = limit
        self.ntasks = ntasks
        self.delay = delay
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if self.mode not in ScraperConfig._MODES:
            raise ValueError(f"Mode must be one of: {', '.join(ScraperConfig._MODES)}")


class DiscordScraper:
    """This instance is by design not thread safe
    To use it in multiple threads use different instances of this class"""

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

        self.batch_size = 100
        self.processed_count = 0
        self.state = self.state_manager.load_state()

        self._lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(self.config.ntasks)


    async def _bounded_call[T](self, call: Awaitable[T]) -> T:
        async with self._sem:
            return await call

    async def set_state(self, message_id: int | str) -> None:
        if not (type(message_id) is int and message_id > 0
                or type(message_id) is str and message_id.strip()):
            return

        async with self._lock:
            self.state.oldest_message_id = int(message_id)
            self.state_manager.save_state(self.state)

    async def processed(self, count: int) -> None:
        if count <= 0: return

        async with self._lock:
            self.processed_count += count

    async def finished(self) -> bool:
        async with self._lock:
            return self.config.limit != -1 and self.processed_count >= self.config.limit

    async def process_attachment(self,
                                 attachments: list[dict[str, str | int]],
                                 media_dir: Path, message_id: str | int) -> list[str]:
        saved_attachments = []
        if await self.finished():
            return saved_attachments

        for att in attachments:
            file_url = att.get("url")
            if file_url is None or type(file_url) is not str:
                continue

            file_name = att.get("filename", "unknown_file")
            dest_path = media_dir / f"{message_id}_{file_name}"

            logger.info(f"Downloading attachment: {file_name}")
            if await self.api.download_media(file_url, dest_path):
                saved_attachments.append(str(dest_path.expanduser().resolve().absolute()))

                await self.processed(self.config.mode == "media")
                if await self.finished():
                    break

        return saved_attachments

    async def process_message(self, message: dict):
        if await self.finished():
            return

        message_id = int(message["id"])
        attachments = message.get("attachments", [])
        has_media = len(attachments) > 0
        has_text = bool(message.get("content", "").strip())

        saved_attachments = []

        # Handle media download if required by mode
        if has_media and self.config.mode in {"media", "both"}:
            media_dir = self.config.output_dir / "media" / self.config.channel_id
            saved_attachments.extend(await self.process_attachment(attachments, media_dir, message_id))

        media_downloaded = len(saved_attachments) > 0

        # if we are saving only media and msg don't have any then we should skip
        if not (self.config.mode == "media" and not media_downloaded):
            self.save_repo.append(message, saved_attachments)

        if self.config.mode == "chats" and has_text:
            await self.processed(1)
        elif self.config.mode == "media" and media_downloaded:
            await self.processed(1)
        elif self.config.mode == "both":
            await self.processed(has_text or media_downloaded)

    async def run(self) -> None:
        logger.info(f"Starting scraper for channel ID: {self.config.channel_id} in mode: [{self.config.mode}] "
                    f"state: {self.state}")

        if await self.finished():
            logger.info(f"Reached configured limit of {self.config.limit} items. Stopping.")

        while not await self.finished():
            messages = list(await self.api.fetch_messages(
                channel_id=self.config.channel_id,
                before=self.state.oldest_message_id,
                limit=self.batch_size,
            ))

            if not messages:
                logger.info("No more messages found in channel history.")
                break

            # process messages in parallel
            await asyncio.gather(*map(lambda m: self._bounded_call(self.process_message(m)), messages))

            # Discord message IDs are time-ordered snowflakes, meaning smaller numbers always represent older messages
            await self.set_state(min(map(
                lambda m: int(m["id"]),
                filter(lambda m: bool((t := type(m.get("id"))) and t in (int, str)), messages))))

            # Avoid hitting API limits too fast
            await asyncio.sleep(self.config.delay)

        logger.info(f"Scraping completed successfully. Total processed count: {self.processed_count}")


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
        "--ntasks",
        type=int, default=10,
        help="Number of simultaneous tasks processed in parallel"
    )
    parser.add_argument(
        "--delay",
        type=float, default=0.5,
        help="Delay between each requests (in seconds)"
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
        delay=args.delay,
        ntasks=args.ntasks,
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
        raise


if __name__ == "__main__":
    asyncio.run(main())
