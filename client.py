from pathlib import Path
from typing import Optional, List, Dict, Any

import httpx, asyncio

from utils import get_logger, sanitize_and_cap_filename


logger = get_logger("ApiClient")


class DiscordApiClient:
    BASE_URL = "https://discord.com/api/v10"

    def __init__(self, token: str):
        self.headers = {
            "Authorization": token,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

    async def fetch_messages(
        self, channel_id: str, before: Optional[str | int] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        url = f"{self.BASE_URL}/channels/{channel_id}/messages"
        params: dict[str, str | int] = {"limit": limit}

        if type(before) is int and before > 0 or type(before) is str and before.strip():
            params["before"] = before

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    url, headers=self.headers, params=params, timeout=30.0
                )

                if response.status_code == 429:
                    retry_after = response.json().get("retry_after", 2)
                    logger.warning(
                        f"Rate limited. Sleeping for {retry_after} seconds..."
                    )
                    await asyncio.sleep(retry_after)
                    return await self.fetch_messages(channel_id, before, limit)

                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"HTTP error occurred: {e.response.status_code} - {e.response.text}"
                )
                raise
            except Exception as e:
                logger.error(f"Error fetching messages: {e}")
                raise

    async def download_media(self, url: str, destination: Path) -> bool:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, timeout=60.0)
                response.raise_for_status()

                destination = sanitize_and_cap_filename(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with open(destination, "wb") as f:
                    f.write(response.content)

                return True
            except Exception as e:
                logger.error(f"Failed to download media from {url}: {e}")
                return False


__all__ = ("DiscordApiClient",)
