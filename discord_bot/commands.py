"""Slash commands for configuring a Discord channel's listing feed.

Commands are server-only and by default need the "Manage Channels" permission
(admins can change who may use them under Server Settings → Integrations).
"""
import logging
from typing import Optional

import discord
from discord import app_commands

import db.queries as queries
from bot.keyboards import LAYOUT_OPTIONS
from config import ALL_DISTRICTS
from db.models import UserPreference
from discord_bot.filters import apply_changes, complete_list, format_prefs

logger = logging.getLogger(__name__)

_FILTERS_HINT = "用 `/filters` 設定條件（只填要改的欄位；數字填 0、文字填 `-` 代表清除）。"
_BASELINE_NOTE = (
    "下一輪掃描（約 1 分鐘內）會先建立基準，回報目前符合的筆數與最新幾筆，"
    "之後只推播新出現的房源。"
)


async def _current_prefs(channel_id: int) -> tuple[UserPreference, bool]:
    channel = await queries.get_discord_channel(channel_id)
    if channel is None:
        return UserPreference(chat_id=channel_id), False
    return channel.prefs, channel.active


def _permission_warning(interaction: discord.Interaction) -> str:
    perms = interaction.app_permissions
    missing = [
        name for name, ok in (
            ("傳送訊息", perms.send_messages),
            ("嵌入連結", perms.embed_links),
        ) if not ok
    ]
    if not missing:
        return ""
    return f"\n\n⚠️ Bot 在這個頻道缺少「{'、'.join(missing)}」權限，推播會失敗，請到頻道設定開啟。"


@app_commands.command(name="subscribe", description="在這個頻道開始推播符合條件的新房源")
@app_commands.guild_only()
@app_commands.default_permissions(manage_channels=True)
async def subscribe(interaction: discord.Interaction) -> None:
    from scheduler.job_manager import ensure_global_job

    await queries.subscribe_discord_channel(interaction.channel_id, interaction.guild_id)
    ensure_global_job(interaction.client.telegram_app)
    prefs, _ = await _current_prefs(interaction.channel_id)
    await interaction.response.send_message(
        f"✅ 這個頻道已開始監控。\n\n**目前條件**\n{format_prefs(prefs)}\n\n"
        f"{_BASELINE_NOTE}\n{_FILTERS_HINT}"
        + _permission_warning(interaction)
    )


@app_commands.command(name="unsubscribe", description="停止在這個頻道推播（條件會保留）")
@app_commands.guild_only()
@app_commands.default_permissions(manage_channels=True)
async def unsubscribe(interaction: discord.Interaction) -> None:
    if await queries.unsubscribe_discord_channel(interaction.channel_id):
        msg = "⏹ 已停止推播。條件已保留，之後 `/subscribe` 即可恢復。"
    else:
        msg = "這個頻道目前沒有在監控。"
    await interaction.response.send_message(msg)


@app_commands.command(name="status", description="查看這個頻道的監控狀態與條件")
@app_commands.guild_only()
@app_commands.default_permissions(manage_channels=True)
async def status(interaction: discord.Interaction) -> None:
    prefs, active = await _current_prefs(interaction.channel_id)
    state = "🟢 監控中" if active else "⚪ 未監控（用 `/subscribe` 開始）"
    await interaction.response.send_message(
        f"**狀態**：{state}\n\n**條件**（全部都要符合）\n{format_prefs(prefs)}\n\n{_FILTERS_HINT}",
        ephemeral=True,
    )


@app_commands.command(name="filters", description="設定這個頻道的推播條件（只填要修改的欄位）")
@app_commands.guild_only()
@app_commands.default_permissions(manage_channels=True)
@app_commands.rename(
    min_rent="最低租金", max_rent="最高租金", min_size="最小坪數",
    districts="行政區", layouts="格局",
    pets="可養寵物", parking="要有車位", elevator="要有電梯",
    include="包含關鍵字", exclude="排除關鍵字",
)
@app_commands.describe(
    min_rent="元，0 = 不限",
    max_rent="元，0 = 不限",
    min_size="坪，0 = 不限",
    districts="可多選，用逗號分隔，例如：大安區,信義區（填 - 清除）",
    layouts="套房、1房、2房、3房+，可多選（填 - 清除）",
    pets="是 = 只要可養寵物的；否 = 不限",
    parking="是 = 只要有車位的；否 = 不限",
    elevator="是 = 只要有電梯的；否 = 不限",
    include="每個關鍵字都要出現，逗號分隔，例如：近捷運,陽台（填 - 清除）",
    exclude="出現任一個就排除，逗號分隔，例如：頂加,雅房（填 - 清除）",
)
async def filters(
    interaction: discord.Interaction,
    min_rent: Optional[app_commands.Range[int, 0, 1_000_000]] = None,
    max_rent: Optional[app_commands.Range[int, 0, 1_000_000]] = None,
    min_size: Optional[app_commands.Range[float, 0, 1000]] = None,
    districts: Optional[str] = None,
    layouts: Optional[str] = None,
    pets: Optional[bool] = None,
    parking: Optional[bool] = None,
    elevator: Optional[bool] = None,
    include: Optional[str] = None,
    exclude: Optional[str] = None,
) -> None:
    prefs, active = await _current_prefs(interaction.channel_id)
    changes = dict(
        min_rent=min_rent, max_rent=max_rent, min_size=min_size,
        districts=districts, layouts=layouts,
        pets=pets, parking=parking, elevator=elevator,
        include=include, exclude=exclude,
    )

    if all(v is None for v in changes.values()):
        await interaction.response.send_message(
            f"**目前條件**（全部都要符合）\n{format_prefs(prefs)}\n\n{_FILTERS_HINT}",
            ephemeral=True,
        )
        return

    try:
        prefs = apply_changes(prefs, **changes)
    except ValueError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
        return

    await queries.save_discord_preference(interaction.channel_id, interaction.guild_id, prefs)
    tail = _BASELINE_NOTE if active else "這個頻道還沒開始監控，用 `/subscribe` 開始。"
    await interaction.response.send_message(
        f"✅ 條件已更新（全部都要符合）\n{format_prefs(prefs)}\n\n{tail}"
    )


@filters.autocomplete("districts")
async def _districts_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=v, value=v) for v in complete_list(current, ALL_DISTRICTS)]


@filters.autocomplete("layouts")
async def _layouts_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=v, value=v) for v in complete_list(current, LAYOUT_OPTIONS)]


@app_commands.command(name="clear_filters", description="清除這個頻道的所有條件（推播全部新房源）")
@app_commands.guild_only()
@app_commands.default_permissions(manage_channels=True)
async def clear_filters(interaction: discord.Interaction) -> None:
    _, active = await _current_prefs(interaction.channel_id)
    await queries.save_discord_preference(
        interaction.channel_id, interaction.guild_id, UserPreference(chat_id=interaction.channel_id)
    )
    tail = _BASELINE_NOTE if active else "這個頻道還沒開始監控，用 `/subscribe` 開始。"
    await interaction.response.send_message(f"🧹 已清除所有條件。\n{tail}")


def register_commands(tree: app_commands.CommandTree) -> None:
    for cmd in (subscribe, unsubscribe, status, filters, clear_filters):
        tree.add_command(cmd)
