"""Discord bot: each subscribed channel gets its own filters (set with slash
commands) and receives matching new listings.

Runs on the same asyncio loop as the Telegram bot — started from main.post_init.
"""
import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from db.models import Listing
from notifiers.discord_webhook import build_embed

if TYPE_CHECKING:
    from telegram.ext import Application

logger = logging.getLogger(__name__)

# How long a push waits for the gateway connection right after startup.
_READY_TIMEOUT = 30


class HouseRenterDiscordBot(discord.Client):
    def __init__(self, telegram_app: "Application"):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        # The scrape job lives on the Telegram application; /subscribe needs it.
        self.telegram_app = telegram_app

    async def setup_hook(self) -> None:
        from discord_bot.commands import register_commands
        register_commands(self.tree)
        synced = await self.tree.sync()
        logger.info("Discord: synced %d slash command(s).", len(synced))

    async def on_ready(self) -> None:
        logger.info("Discord bot logged in as %s (%d server(s)).", self.user, len(self.guilds))

    async def _channel(self, channel_id: int):
        await asyncio.wait_for(self.wait_until_ready(), _READY_TIMEOUT)
        return self.get_channel(channel_id) or await self.fetch_channel(channel_id)

    async def send_listing(self, channel_id: int, listing: Listing) -> None:
        channel = await self._channel(channel_id)
        await channel.send(embed=discord.Embed.from_dict(build_embed(listing)))

    async def send_text(self, channel_id: int, text: str) -> None:
        channel = await self._channel(channel_id)
        await channel.send(text[:2000])


_bot: HouseRenterDiscordBot | None = None
_task: asyncio.Task | None = None


def get_discord_bot() -> HouseRenterDiscordBot | None:
    """None when DISCORD_BOT_TOKEN is unset or the bot has stopped (e.g. bad token)."""
    if _bot is None or _task is None or _task.done():
        return None
    return _bot


def _on_bot_stopped(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        logger.error("Discord bot stopped: %s", task.exception(), exc_info=task.exception())


async def start_discord_bot(telegram_app: "Application") -> None:
    global _bot, _task
    from config import DISCORD_BOT_TOKEN
    if not DISCORD_BOT_TOKEN:
        return
    _bot = HouseRenterDiscordBot(telegram_app)
    _task = asyncio.create_task(_bot.start(DISCORD_BOT_TOKEN))
    _task.add_done_callback(_on_bot_stopped)
    logger.info("Discord bot starting…")


async def stop_discord_bot() -> None:
    if _bot is not None and not _bot.is_closed():
        await _bot.close()
