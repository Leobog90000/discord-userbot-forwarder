import os
import json
import asyncio
from datetime import datetime, timezone

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# --- НАСТРОЙКА ---
TOKEN = os.environ.get('USER_TOKEN')
if not TOKEN:
    print("Ошибка: переменная окружения USER_TOKEN не задана.")
    exit()

# --- ЗАГРУЗКА КОНФИГУРАЦИИ ---
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
except FileNotFoundError:
    print("Файл config.json не найден — стартуем без базовых пар (добавляйте через !serverres add).")
    config = {"source_servers": []}

# --- СОЗДАНИЕ СЛОВАРЯ СООТВЕТСТВИЙ ИЗ config.json ---
CHANNEL_MAPPING = {}
for server in config.get('source_servers', []):
    for ch in server.get('channels', []):
        try:
            source_id = int(ch['source_id'])
            target_id = int(ch['target_id'])
            CHANNEL_MAPPING[source_id] = target_id
        except (KeyError, ValueError):
            print(f"Ошибка в канале: {ch}")

if not CHANNEL_MAPPING:
    print("Предупреждение: маппинг каналов из config.json пустой.")

print(f"Загружено пар каналов из config.json: {len(CHANNEL_MAPPING)}")

# --- БАЗОВЫЕ ВЛАДЕЛЬЦЫ (зашиты в коде, командой не удаляются) ---
OWNER_IDS = {
    111111111111111111,  # @username (аккаунт бота / the bot account itself)
    222222222222222222,  # @username (ваш основной аккаунт / your main account)
}

# --- ФАЙЛЫ ДЛЯ ХРАНЕНИЯ ТОГО, ЧТО ДОБАВЛЕНО КОМАНДАМИ (переживает перезапуск) ---
ADMINS_FILE = "admins_extra.json"
MAPPINGS_FILE = "mappings_extra.json"


def load_json_set(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return {int(x) for x in json.load(f)}
        except Exception as e:
            print(f"Не удалось прочитать {path}: {e}")
    return set()


def save_json_set(path, data_set):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(list(data_set), f)
    except Exception as e:
        print(f"Не удалось сохранить {path}: {e}")


def load_json_dict(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                return {int(k): int(v) for k, v in raw.items()}
        except Exception as e:
            print(f"Не удалось прочитать {path}: {e}")
    return {}


def save_json_dict(path, data_dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in data_dict.items()}, f)
    except Exception as e:
        print(f"Не удалось сохранить {path}: {e}")


# Дополнительные админы/пары, добавленные через !adminres и !serverres
EXTRA_ADMIN_IDS = load_json_set(ADMINS_FILE)
EXTRA_MAPPINGS = load_json_dict(MAPPINGS_FILE)

# Рабочие множества, с которыми сверяемся в коде
ADMIN_IDS = set(OWNER_IDS) | EXTRA_ADMIN_IDS
CHANNEL_MAPPING.update(EXTRA_MAPPINGS)

print(f"Всего администраторов: {len(ADMIN_IDS)} (из них базовых: {len(OWNER_IDS)})")
print(f"Всего пар каналов: {len(CHANNEL_MAPPING)}")

# --- НАСТРОЙКИ ВЫБОРОЧНОЙ ПЕРЕСЫЛКИ ---
SYNC_DELAY_SECONDS = 1.5      # пауза между сообщениями, чтобы не спамить/не попасть под анти-спам
SYNC_MAX_MESSAGES = 500       # предохранитель от случайной пересылки огромной истории

# --- НАСТРОЙКА КЛИЕНТА ---
bot = commands.Bot(
    command_prefix='!',
    self_bot=True,
)

# discord.py-self при self_bot=True по умолчанию через _skip_check игнорирует
# команды от всех, кроме самого аккаунта-владельца токена. Авторизация у нас
# и так своя — по ADMIN_IDS в каждой команде и в on_message, — так что
# отключаем эту встроенную проверку полностью.
bot._skip_check = lambda author_id, self_id: False


@bot.event
async def on_ready():
    print(f'Вошли как {bot.user} (self-bot режим).')
    print(f'Отслеживается каналов: {len(CHANNEL_MAPPING)}')


MAX_ATTACHMENT_MB = 20  # лимит для НЕ-картинок: если больше — не перезаливаем, а даём ссылку
MAX_ATTACHMENT_BYTES = MAX_ATTACHMENT_MB * 1024 * 1024

# Картинки и гифки всегда пытаемся отправить как настоящее вложение,
# независимо от размера — на них MAX_ATTACHMENT_MB не действует.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".apng"}


def is_image_attachment(attachment):
    ext = os.path.splitext(attachment.filename)[1].lower()
    return ext in IMAGE_EXTENSIONS


async def download_attachments(message):
    """
    Возвращает (files, big_links).
    files     — discord.File для вложений, которые нужно перезалить как файл
                (картинки/гифки — всегда; остальное — если укладывается в MAX_ATTACHMENT_MB).
    big_links — готовые строки-ссылки для вложений, которые решили не перезаливать
                (не влезли в лимит, либо скачать всё равно не удалось).
    """
    files = []
    big_links = []
    for attachment in message.attachments:
        is_image = is_image_attachment(attachment)

        # Не-картинки, не влезающие в лимит — ссылкой, без попытки скачивания.
        if not is_image and attachment.size > MAX_ATTACHMENT_BYTES:
            size_mb = attachment.size / (1024 * 1024)
            big_links.append(f"📎 {attachment.filename} ({size_mb:.1f} МБ): {attachment.url}")
            continue

        try:
            # use_cached=True шёл бы через медиа-прокси Discord, а тот умеет
            # отдавать только изображения/видео. Для произвольных файлов
            # (архивы, документы и т.д.) это даёт 415 Unsupported Media Type —
            # поэтому качаем напрямую с обычного CDN.
            file = await attachment.to_file(use_cached=False)
            files.append(file)
        except Exception as e:
            print(f"Не удалось скачать вложение {attachment.filename}: {e}, отправлю ссылкой.")
            size_mb = attachment.size / (1024 * 1024)
            big_links.append(f"📎 {attachment.filename} ({size_mb:.1f} МБ): {attachment.url}")
    return files, big_links


async def forward_single_message(message, target_channel):
    """
    Общая логика пересылки одного сообщения (используется и для live, и для истории).

    Пересылается только исходное содержимое сообщения — без указания автора,
    канала или сервера-источника. Если в тексте есть ссылка (например, на
    YouTube), она остаётся как обычная ссылка в теле сообщения — Discord сам
    сгенерирует превью для неё через пару секунд после отправки, точно так же,
    как если бы её вставил живой пользователь. Отдельно собирать embed вручную
    не нужно — это может привести к задвоению превью.
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
        print(f"Нет прав для отправки в канал {target_channel.id}.")
    except Exception as e:
        print(f"Ошибка при отправке: {e}")
    return False


def parse_dt(raw):
    """Парсит дату в формате YYYY-MM-DD или YYYY-MM-DD HH:MM (UTC)."""
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Не удалось разобрать дату: {raw!r}. Используйте YYYY-MM-DD или YYYY-MM-DD HH:MM")


@bot.event
async def on_command_error(ctx, error):
    """Чтобы ошибки команд не пропадали молча в консоли."""
    if isinstance(error, commands.CommandNotFound):
        return
    await ctx.send(f"Ошибка команды: {error}")
    print(f"Ошибка команды {ctx.command}: {error}")


@bot.event
async def on_message(message):
    # Сообщения от админов — только команды, без пересылки
    if message.author.id in ADMIN_IDS:
        await bot.process_commands(message)
        return

    # Игнорируем ЛС от не-админов
    if not message.guild:
        return

    # Пересылка "живых" сообщений из отслеживаемых каналов
    if message.channel.id in CHANNEL_MAPPING:
        target_channel_id = CHANNEL_MAPPING[message.channel.id]
        target_channel = bot.get_channel(target_channel_id)

        if not target_channel:
            print(f"Целевой канал с ID {target_channel_id} не найден.")
        else:
            await forward_single_message(message, target_channel)

    await bot.process_commands(message)


# --- КОМАНДЫ ---
@bot.command(name="helpres")
async def helpres(ctx):
    if ctx.author.id not in ADMIN_IDS:
        return

    text = (
        "**Команды бота**\n\n"
        "`!aboutres` — статус бота и список активных пар пересылки.\n\n"
        "`!helpres` — это сообщение.\n\n"
        "**Администраторы**\n"
        "`!adminres list` — список администраторов.\n"
        "`!adminres add <user_id>` — добавить администратора.\n"
        "`!adminres remove <user_id>` — убрать администратора (кроме базовых).\n"
        "Пример: `!adminres add 123456789012345678`\n\n"
        "**Пары каналов (источник → цель)**\n"
        "`!serverres list` — список пар пересылки.\n"
        "`!serverres add <source_id> <target_id>` — добавить пару каналов.\n"
        "`!serverres remove <source_id>` — убрать пару каналов.\n"
        "Пример: `!serverres add 111111111111111111 222222222222222222`\n\n"
        "**Выборочная пересылка истории**\n"
        "`!syncrange <source_id|source1,source2,...|all> <start> [end] [limit]`\n"
        "Даты: YYYY-MM-DD или YYYY-MM-DD HH:MM (UTC). limit — на каждый канал.\n"
        "Примеры:\n"
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
    ) or "  (пусто)"
    text = (
        f"**О боте**\n"
        f"Пересылает сообщения между каналами (без указания автора/канала-источника).\n"
        f"Режим: self-bot (discord-py-self).\n"
        f"Администраторов: {len(ADMIN_IDS)}\n"
        f"Активных пар: {len(CHANNEL_MAPPING)}\n"
        f"{pairs}"
    )
    await ctx.send(text)


@bot.command(name="adminres")
async def adminres(ctx, action: str = None, user_id: int = None):
    """
    Управление списком администраторов бота.

    !adminres list                — показать текущих админов
    !adminres add <user_id>       — добавить админа
    !adminres remove <user_id>    — убрать добавленного админа
                                     (базовых из OWNER_IDS удалить нельзя)
    """
    if ctx.author.id not in ADMIN_IDS:
        return

    if action is None:
        action = "list"

    if action == "list":
        lines = "\n".join(
            f"  {uid}" + (" (базовый)" if uid in OWNER_IDS else "")
            for uid in ADMIN_IDS
        ) or "  (пусто)"
        await ctx.send(f"**Администраторы бота:**\n{lines}")
        return

    if action == "add":
        if user_id is None:
            await ctx.send("Использование: `!adminres add <user_id>`")
            return
        if user_id in ADMIN_IDS:
            await ctx.send("Этот пользователь уже администратор.")
            return
        ADMIN_IDS.add(user_id)
        EXTRA_ADMIN_IDS.add(user_id)
        save_json_set(ADMINS_FILE, EXTRA_ADMIN_IDS)
        await ctx.send(f"Добавлен администратор: {user_id}")
        return

    if action == "remove":
        if user_id is None:
            await ctx.send("Использование: `!adminres remove <user_id>`")
            return
        if user_id in OWNER_IDS:
            await ctx.send("Нельзя удалить базового владельца (он зашит в коде).")
            return
        if user_id not in ADMIN_IDS:
            await ctx.send("Этот пользователь не в списке администраторов.")
            return
        ADMIN_IDS.discard(user_id)
        EXTRA_ADMIN_IDS.discard(user_id)
        save_json_set(ADMINS_FILE, EXTRA_ADMIN_IDS)
        await ctx.send(f"Удалён администратор: {user_id}")
        return

    await ctx.send("Неизвестное действие. Используйте: `list` / `add` / `remove`")


@bot.command(name="serverres")
async def serverres(ctx, action: str = None, source_id: int = None, target_id: int = None):
    """
    Управление парами каналов для пересылки (источник -> цель).

    !serverres list                          — показать текущие пары
    !serverres add <source_id> <target_id>   — добавить пару
    !serverres remove <source_id>            — убрать пару
    """
    if ctx.author.id not in ADMIN_IDS:
        return

    if action is None:
        action = "list"

    if action == "list":
        pairs = "\n".join(
            f"  <#{src}> → <#{tgt}>" for src, tgt in CHANNEL_MAPPING.items()
        ) or "  (пусто)"
        await ctx.send(f"**Активные пары пересылки:**\n{pairs}")
        return

    if action == "add":
        if source_id is None or target_id is None:
            await ctx.send("Использование: `!serverres add <source_id> <target_id>`")
            return
        CHANNEL_MAPPING[source_id] = target_id
        EXTRA_MAPPINGS[source_id] = target_id
        save_json_dict(MAPPINGS_FILE, EXTRA_MAPPINGS)
        await ctx.send(f"Добавлена пара: <#{source_id}> → <#{target_id}>")
        return

    if action == "remove":
        if source_id is None:
            await ctx.send("Использование: `!serverres remove <source_id>`")
            return
        if source_id not in CHANNEL_MAPPING:
            await ctx.send("Такой пары нет.")
            return

        CHANNEL_MAPPING.pop(source_id, None)

        if source_id in EXTRA_MAPPINGS:
            EXTRA_MAPPINGS.pop(source_id, None)
            save_json_dict(MAPPINGS_FILE, EXTRA_MAPPINGS)
            await ctx.send(f"Пара с источником <#{source_id}> удалена.")
        else:
            # Эта пара пришла из config.json — уберётся из рабочего набора
            # только до перезапуска, при следующем старте подтянется снова.
            await ctx.send(
                f"Пара с источником <#{source_id}> убрана до перезапуска бота.\n"
                f"Она задана в config.json, так что уберите её и там, "
                f"иначе после перезапуска она вернётся."
            )
        return

    await ctx.send("Неизвестное действие. Используйте: `list` / `add` / `remove`")


@bot.command(name="syncrange")
async def syncrange(ctx, sources: str, start: str, end: str = None, limit: int = None):
    """
    Выборочная пересылка сообщений из истории одного или нескольких каналов за период.

    Использование:
      !syncrange <source_id>                          <start> [end] [limit]
      !syncrange <source_id1>,<source_id2>,...         <start> [end] [limit]
      !syncrange all                                   <start> [end] [limit]

    sources — один ID, несколько ID через запятую (без пробелов), либо "all"
              (все каналы, которые сейчас есть в маппинге).
    Даты в формате: YYYY-MM-DD или YYYY-MM-DD HH:MM (UTC)
    Если end не указан — берётся текущий момент.
    limit — максимум сообщений НА КАЖДЫЙ канал (по умолчанию SYNC_MAX_MESSAGES).

    Примеры:
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
                "Не удалось разобрать список каналов. Через запятую без пробелов, "
                "например: `111111111111111111,222222222222222222`"
            )
            return

    if not source_ids:
        await ctx.send("Не указано ни одного канала-источника.")
        return

    missing = [sid for sid in source_ids if sid not in CHANNEL_MAPPING]
    if missing:
        await ctx.send(
            "Эти каналы не найдены в маппинге: "
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
        await ctx.send("Начальная дата должна быть раньше конечной.")
        return

    max_messages = limit or SYNC_MAX_MESSAGES

    await ctx.send(
        f"Начинаю выборочную пересылку по {len(source_ids)} источник(ам).\n"
        f"Период: {start_dt.isoformat()} → {end_dt.isoformat()}\n"
        f"Лимит сообщений на канал: {max_messages}"
    )

    total_scanned = 0
    total_sent = 0

    for source_id in source_ids:
        source_channel = bot.get_channel(source_id)
        if not source_channel:
            await ctx.send(f"⚠️ Не удалось получить канал-источник {source_id}, пропускаю.")
            continue

        target_channel = bot.get_channel(CHANNEL_MAPPING[source_id])
        if not target_channel:
            await ctx.send(f"⚠️ Не удалось получить целевой канал для {source_id}, пропускаю.")
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
            await ctx.send(f"⚠️ Нет прав на чтение истории канала {source_id}, пропускаю.")
            continue
        except Exception as e:
            await ctx.send(f"⚠️ Ошибка при синхронизации канала {source_id}: {e}")
            continue

        total_scanned += scanned
        total_sent += sent
        await ctx.send(
            f"✅ <#{source_id}> → <#{target_channel.id}>: просканировано {scanned}, переслано {sent}."
        )

    await ctx.send(f"Готово. Всего просканировано: {total_scanned}, всего переслано: {total_sent}.")


# --- ЗАПУСК ---
bot.run(TOKEN)
