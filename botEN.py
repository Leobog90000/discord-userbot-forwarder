import os
import json
import asyncio
from datetime import datetime, timezone

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# --- SETUP ---
TOKEN = os.environ.get('USER_TOKEN')
if not TOKEN:
    print("Error: the USER_TOKEN environment variable is not set.")
    exit()

# --- LOAD CONFIGURATION ---
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
except FileNotFoundError:
    print("config.json not found — starting without base pairs (add them with !serverres add).")
    config = {"source_servers": []}

# --- BUILD THE CHANNEL MAPPING FROM config.json ---
CHANNEL_MAPPING = {}
for server in config.get('source_servers', []):
    for ch in server.get('channels', []):
        try:
            source_id = int(ch['source_id'])
            target_id = int(ch['target_id'])
            CHANNEL_MAPPING[source_id] = target_id
        except (KeyError, ValueError):
            print(f"Invalid channel entry: {ch}")

if not CHANNEL_MAPPING:
    print("Warning: the channel mapping from config.json is empty.")

print(f"Channel pairs loaded from config.json: {len(CHANNEL_MAPPING)}")

# --- BASE OWNERS (hardcoded, cannot be removed by command) ---
OWNER_IDS = {
    111111111111111111,  # @username (the bot account itself)
    222222222222222222,  # @username (your main account)
}

# --- FILES FOR DATA ADDED VIA COMMANDS (survives restarts) ---
ADMINS_FILE = "admins_extra.json"
MAPPINGS_FILE = "mappings_extra.json"


def load_json_set(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return {int(x) for x in json.load(f)}
        except Exception as e:
            print(f"Failed to read {path}: {e}")
    return set()


def save_json_set(path, data_set):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(list(data_set), f)
    except Exception as e:
        print(f"Failed to save {path}: {e}")


def load_json_dict(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                return {int(k): int(v) for k, v in raw.items()}
        except Exception as e:
            print(f"Failed to read {path}: {e}")
    return {}


def save_json_dict(path, data_dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in data_dict.items()}, f)
    except Exception as e:
        print(f"Failed to save {path}: {e}")


# Extra admins/pairs added via !adminres and !serverres
EXTRA_ADMIN_IDS = load_json_set(ADMINS_FILE)
EXTRA_MAPPINGS = load_json_dict(MAPPINGS_FILE)

# Working sets used throughout the code
ADMIN_IDS = set(OWNER_IDS) | EXTRA_ADMIN_IDS
CHANNEL_MAPPING.update(EXTRA_MAPPINGS)

print(f"Total administrators: {len(ADMIN_IDS)} (base owners: {len(OWNER_IDS)})")
print(f"Total channel pairs: {len(CHANNEL_MAPPING)}")

# --- SELECTIVE HISTORY SYNC SETTINGS ---
SYNC_DELAY_SECONDS = 1.5      # pause between messages to avoid spam / anti-spam triggers
SYNC_MAX_MESSAGES = 500       # safeguard against accidentally forwarding a huge history

# --- CLIENT SETUP ---
bot = commands.Bot(
    command_prefix='!',
    self_bot=True,
)

# With self_bot=True, discord.py-self ignores commands from everyone except the
# token owner's own account (via _skip_check). Authorization here is handled by
# ADMIN_IDS in every command and in on_message, so we disable that built-in
# check completely.
bot._skip_check = lambda author_id, self_id: False


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (self-bot mode).')
    print(f'Channels being watched: {len(CHANNEL_MAPPING)}')


MAX_ATTACHMENT_MB = 20  # limit for NON-images: larger files are not re-uploaded, a link is sent instead
MAX_ATTACHMENT_BYTES = MAX_ATTACHMENT_MB * 1024 * 1024

# Images and GIFs are always re-uploaded as real attachments,
# regardless of size — MAX_ATTACHMENT_MB does not apply to them.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".apng"}


def is_image_attachment(attachment):
    ext = os.path.splitext(attachment.filename)[1].lower()
    return ext in IMAGE_EXTENSIONS


async def download_attachments(message):
    """
    Returns (files, big_links).
    files     — discord.File objects for attachments that should be re-uploaded
                (images/GIFs always; everything else if it fits MAX_ATTACHMENT_MB).
    big_links — ready-made link strings for attachments we decided not to re-upload
                (too large, or the download failed anyway).
    """
    files = []
    big_links = []
    for attachment in message.attachments:
        is_image = is_image_attachment(attachment)

        # Non-images over the limit: send as a link, don't even try to download.
        if not is_image and attachment.size > MAX_ATTACHMENT_BYTES:
            size_mb = attachment.size / (1024 * 1024)
            big_links.append(f"📎 {attachment.filename} ({size_mb:.1f} MB): {attachment.url}")
            continue

        try:
            # use_cached=True would go through Discord's media proxy, which only
            # serves images/video. For arbitrary files (archives, documents, etc.)
            # that returns 415 Unsupported Media Type — so we download directly
            # from the regular CDN.
            file = await attachment.to_file(use_cached=False)
            files.append(file)
        except Exception as e:
            print(f"Failed to download attachment {attachment.filename}: {e}, sending as a link.")
            size_mb = attachment.size / (1024 * 1024)
            big_links.append(f"📎 {attachment.filename} ({size_mb:.1f} MB): {attachment.url}")
    return files, big_links


async def forward_single_message(message, target_channel):
    """
    Shared logic for forwarding a single message (used for both live messages and history).

    Only the original message content is forwarded — no author, channel or source
    server is shown. If the text contains a link (e.g. YouTube), it stays a plain
    link in the message body — Discord generates the preview itself a few seconds
    after sending, exactly as if a real user had pasted it. There is no need to
    build an embed manually — that could result in duplicated previews.
    """
    attachments, big_links = await download_attachments(message)
    body = message.content or ""

    if big_links:
        extra = "\n".join(big_links)
        body = f"{body}\n{extra}".strip() if body else extra

    if not body and not attachments:
        return False

    try:
        await target_channel.send(body, files=attachments)
        return True
    except discord.Forbidden:
        print(f"No permission to send to channel {target_channel.id}.")
    except Exception as e:
        print(f"Error while sending: {e}")
    return False


def parse_dt(raw):
    """Parses a date in the format YYYY-MM-DD or YYYY-MM-DD HH:MM (UTC)."""
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Could not parse date: {raw!r}. Use YYYY-MM-DD or YYYY-MM-DD HH:MM")


@bot.event
async def on_command_error(ctx, error):
    """So command errors don't silently vanish in the console."""
    if isinstance(error, commands.CommandNotFound):
        return
    await ctx.send(f"Command error: {error}")
    print(f"Command error {ctx.command}: {error}")


@bot.event
async def on_message(message):
    # Messages from admins — commands only, no forwarding
    if message.author.id in ADMIN_IDS:
        await bot.process_commands(message)
        return

    # Ignore DMs from non-admins
    if not message.guild:
        return

    # Forward "live" messages from watched channels
    if message.channel.id in CHANNEL_MAPPING:
        target_channel_id = CHANNEL_MAPPING[message.channel.id]
        target_channel = bot.get_channel(target_channel_id)

        if not target_channel:
            print(f"Target channel with ID {target_channel_id} not found.")
        else:
            await forward_single_message(message, target_channel)

    await bot.process_commands(message)


# --- COMMANDS ---
@bot.command(name="helpres")
async def helpres(ctx):
    if ctx.author.id not in ADMIN_IDS:
        return

    text = (
        "**Bot commands**\n\n"
        "`!aboutres` — bot status and the list of active forwarding pairs.\n\n"
        "`!helpres` — this message.\n\n"
        "**Administrators**\n"
        "`!adminres list` — list administrators.\n"
        "`!adminres add <user_id>` — add an administrator.\n"
        "`!adminres remove <user_id>` — remove an administrator (except base owners).\n"
        "Example: `!adminres add 123456789012345678`\n\n"
        "**Channel pairs (source → target)**\n"
        "`!serverres list` — list forwarding pairs.\n"
        "`!serverres add <source_id> <target_id>` — add a channel pair.\n"
        "`!serverres remove <source_id>` — remove a channel pair.\n"
        "Example: `!serverres add 111111111111111111 222222222222222222`\n\n"
        "**Selective history forwarding**\n"
        "`!syncrange <source_id|source1,source2,...|all> <start> [end] [limit]`\n"
        "Dates: YYYY-MM-DD or YYYY-MM-DD HH:MM (UTC). limit — per channel.\n"
        "Examples:\n"
        "`!syncrange 111111111111111111 2025-01-01 2025-01-05`\n"
        "`!syncrange 111111111111111111,222222222222222222 2025-01-01 2025-01-05`\n"
        "`!syncrange all 2025-01-01 2025-01-05 200`"
    )
    await ctx.send(text)


@bot.command(name="aboutres")
async def aboutres(ctx):
    if ctx.author.id not in ADMIN_IDS:
        return

    pairs = "\n".join(
        f"  <#{src}> → <#{tgt}>" for src, tgt in CHANNEL_MAPPING.items()
    ) or "  (empty)"
    text = (
        f"**About the bot**\n"
        f"Forwards messages between channels (without showing the author/source channel).\n"
        f"Mode: self-bot (discord-py-self).\n"
        f"Administrators: {len(ADMIN_IDS)}\n"
        f"Active pairs: {len(CHANNEL_MAPPING)}\n"
        f"{pairs}"
    )
    await ctx.send(text)


@bot.command(name="adminres")
async def adminres(ctx, action: str = None, user_id: int = None):
    """
    Manage the bot's administrator list.

    !adminres list                — show current admins
    !adminres add <user_id>       — add an admin
    !adminres remove <user_id>    — remove an added admin
                                     (base owners from OWNER_IDS cannot be removed)
    """
    if ctx.author.id not in ADMIN_IDS:
        return

    if action is None:
        action = "list"

    if action == "list":
        lines = "\n".join(
            f"  {uid}" + (" (base)" if uid in OWNER_IDS else "")
            for uid in ADMIN_IDS
        ) or "  (empty)"
        await ctx.send(f"**Bot administrators:**\n{lines}")
        return

    if action == "add":
        if user_id is None:
            await ctx.send("Usage: `!adminres add <user_id>`")
            return
        if user_id in ADMIN_IDS:
            await ctx.send("This user is already an administrator.")
            return
        ADMIN_IDS.add(user_id)
        EXTRA_ADMIN_IDS.add(user_id)
        save_json_set(ADMINS_FILE, EXTRA_ADMIN_IDS)
        await ctx.send(f"Administrator added: {user_id}")
        return

    if action == "remove":
        if user_id is None:
            await ctx.send("Usage: `!adminres remove <user_id>`")
            return
        if user_id in OWNER_IDS:
            await ctx.send("Cannot remove a base owner (hardcoded in the source).")
            return
        if user_id not in ADMIN_IDS:
            await ctx.send("This user is not in the administrator list.")
            return
        ADMIN_IDS.discard(user_id)
        EXTRA_ADMIN_IDS.discard(user_id)
        save_json_set(ADMINS_FILE, EXTRA_ADMIN_IDS)
        await ctx.send(f"Administrator removed: {user_id}")
        return

    await ctx.send("Unknown action. Use: `list` / `add` / `remove`")


@bot.command(name="serverres")
async def serverres(ctx, action: str = None, source_id: int = None, target_id: int = None):
    """
    Manage channel pairs for forwarding (source -> target).

    !serverres list                          — show current pairs
    !serverres add <source_id> <target_id>   — add a pair
    !serverres remove <source_id>            — remove a pair
    """
    if ctx.author.id not in ADMIN_IDS:
        return

    if action is None:
        action = "list"

    if action == "list":
        pairs = "\n".join(
            f"  <#{src}> → <#{tgt}>" for src, tgt in CHANNEL_MAPPING.items()
        ) or "  (empty)"
        await ctx.send(f"**Active forwarding pairs:**\n{pairs}")
        return

    if action == "add":
        if source_id is None or target_id is None:
            await ctx.send("Usage: `!serverres add <source_id> <target_id>`")
            return
        CHANNEL_MAPPING[source_id] = target_id
        EXTRA_MAPPINGS[source_id] = target_id
        save_json_dict(MAPPINGS_FILE, EXTRA_MAPPINGS)
        await ctx.send(f"Pair added: <#{source_id}> → <#{target_id}>")
        return

    if action == "remove":
        if source_id is None:
            await ctx.send("Usage: `!serverres remove <source_id>`")
            return
        if source_id not in CHANNEL_MAPPING:
            await ctx.send("No such pair.")
            return

        CHANNEL_MAPPING.pop(source_id, None)

        if source_id in EXTRA_MAPPINGS:
            EXTRA_MAPPINGS.pop(source_id, None)
            save_json_dict(MAPPINGS_FILE, EXTRA_MAPPINGS)
            await ctx.send(f"Pair with source <#{source_id}> removed.")
        else:
            # This pair came from config.json — it is only removed from the
            # working set until restart and will be loaded again on next start.
            await ctx.send(
                f"Pair with source <#{source_id}> removed until the bot restarts.\n"
                f"It is defined in config.json, so remove it there as well, "
                f"otherwise it will come back after a restart."
            )
        return

    await ctx.send("Unknown action. Use: `list` / `add` / `remove`")


@bot.command(name="syncrange")
async def syncrange(ctx, sources: str, start: str, end: str = None, limit: int = None):
    """
    Selectively forward history from one or more channels for a given period.

    Usage:
      !syncrange <source_id>                          <start> [end] [limit]
      !syncrange <source_id1>,<source_id2>,...         <start> [end] [limit]
      !syncrange all                                   <start> [end] [limit]

    sources — a single ID, several IDs separated by commas (no spaces), or "all"
              (every channel currently in the mapping).
    Date format: YYYY-MM-DD or YYYY-MM-DD HH:MM (UTC)
    If end is omitted, the current moment is used.
    limit — maximum messages PER CHANNEL (default: SYNC_MAX_MESSAGES).

    Examples:
      !syncrange 123456789012345678 2025-01-01 2025-01-05
      !syncrange 123456789012345678,987654321098765432 2025-01-01 2025-01-05
      !syncrange all 2025-01-01 2025-01-05 200
    """
    if ctx.author.id not in ADMIN_IDS:
        return

    if sources.lower() == "all":
        source_ids = list(CHANNEL_MAPPING.keys())
    else:
        try:
            source_ids = [int(x.strip()) for x in sources.split(",") if x.strip()]
        except ValueError:
            await ctx.send(
                "Could not parse the channel list. Use commas without spaces, "
                "e.g. `111111111111111111,222222222222222222`"
            )
            return

    if not source_ids:
        await ctx.send("No source channels specified.")
        return

    missing = [sid for sid in source_ids if sid not in CHANNEL_MAPPING]
    if missing:
        await ctx.send(
            "These channels were not found in the mapping: "
            + ", ".join(str(m) for m in missing)
        )
        return

    try:
        start_dt = parse_dt(start)
        end_dt = parse_dt(end) if end else datetime.now(timezone.utc)
    except ValueError as e:
        await ctx.send(str(e))
        return

    if start_dt >= end_dt:
        await ctx.send("The start date must be earlier than the end date.")
        return

    max_messages = limit or SYNC_MAX_MESSAGES

    await ctx.send(
        f"Starting selective forwarding for {len(source_ids)} source(s).\n"
        f"Period: {start_dt.isoformat()} → {end_dt.isoformat()}\n"
        f"Message limit per channel: {max_messages}"
    )

    total_scanned = 0
    total_sent = 0

    for source_id in source_ids:
        source_channel = bot.get_channel(source_id)
        if not source_channel:
            await ctx.send(f"⚠️ Could not get source channel {source_id}, skipping.")
            continue

        target_channel = bot.get_channel(CHANNEL_MAPPING[source_id])
        if not target_channel:
            await ctx.send(f"⚠️ Could not get the target channel for {source_id}, skipping.")
            continue

        sent = 0
        scanned = 0
        try:
            async for hist_message in source_channel.history(
                after=start_dt, before=end_dt, oldest_first=True, limit=max_messages
            ):
                scanned += 1
                ok = await forward_single_message(hist_message, target_channel)
                if ok:
                    sent += 1
                await asyncio.sleep(SYNC_DELAY_SECONDS)
        except discord.Forbidden:
            await ctx.send(f"⚠️ No permission to read history of channel {source_id}, skipping.")
            continue
        except Exception as e:
            await ctx.send(f"⚠️ Error while syncing channel {source_id}: {e}")
            continue

        total_scanned += scanned
        total_sent += sent
        await ctx.send(
            f"✅ <#{source_id}> → <#{target_channel.id}>: scanned {scanned}, forwarded {sent}."
        )

    await ctx.send(f"Done. Total scanned: {total_scanned}, total forwarded: {total_sent}.")


# --- RUN ---
bot.run(TOKEN)
